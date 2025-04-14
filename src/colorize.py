import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import h5py
import time
from dotenv import load_dotenv

from loguru import logger
from rasterio.features import rasterize
from affine import Affine
import pickle
from shapely.ops import unary_union

from utils import get_env_var, print_elapsed_time

# Load environment variables from .env file
load_dotenv()

# ===== Configuration Parameters =====
# Visualization Parameters
COLOR_PALETTE = get_env_var("COLOR_PALETTE", "custom")
GRADIENT_START = get_env_var("GRADIENT_START", "#050e25")
GRADIENT_END = get_env_var("GRADIENT_END", "#fec119")
BACKGROUND_COLOR = get_env_var("BACKGROUND_COLOR", "255,255,255")
HEXAGONS_PICKLE = get_env_var("HEXAGONS_PICKLE", "output/hexagons.pkl")
COLORIZED_PLOT = get_env_var("COLORIZED_PLOT", "output/colorized.png")
COLORIZED_H5 = get_env_var("COLORIZED_H5", "output/matrices.h5")
COLORMAP_PLOT = get_env_var("COLORMAP_PLOT", "output/colormap.png")

# Technical Parameters
RASTER_RESOLUTION = get_env_var("RASTER_RESOLUTION", 0.001, float)
HEXAGON_BUFFER = get_env_var("HEXAGON_BUFFER", 0.0001, float)  # Small buffer size in same units as geometry

# Parse background color from 0-255 range to 0-1 range
R, G, B = map(int, BACKGROUND_COLOR.split(','))
BACKGROUND_RGB = [R/255, G/255, B/255]


def get_colormap():
    """Get the colormap based on configuration."""
    if COLOR_PALETTE == "custom":
        # Create a custom colormap with intermediate points for smoother transitions
        colors = [
            GRADIENT_START,  # Start color
            GRADIENT_START,  # Hold start color
            GRADIENT_END     # Hold end color
        ]
        positions = [0.0, 0.1, 1.0]  # Control points for color transitions
        return LinearSegmentedColormap.from_list("custom", list(zip(positions, colors)))
    else:
        return plt.get_cmap(COLOR_PALETTE)


def plot_colormap(cmap):
    """Plot and save the colormap."""
    plt.figure(figsize=(8, 1))
    gradient = np.linspace(0, 1, 256)
    gradient = np.vstack((gradient, gradient))
    plt.imshow(gradient, aspect='auto', cmap=cmap)
    plt.axis('off')
    plt.title('Color Gradient')
    plt.savefig(COLORMAP_PLOT, bbox_inches='tight', dpi=300)
    plt.close()


def load_hexagon_data(hexagonized_data=None):
    """Load hexagonized data from file if not provided."""
    if hexagonized_data is None:
        try:
            with open(HEXAGONS_PICKLE, 'rb') as f:
                hexagonized_data = pickle.load(f)
            logger.info(f"Loaded hexagonized data from {HEXAGONS_PICKLE}")
        except FileNotFoundError:
            logger.error(f"Hexagonized data not found at {HEXAGONS_PICKLE}. Please run hexagonize.py first or provide data.")
            return None
    return hexagonized_data


def calculate_raster_dimensions(hex_gdf):
    """Calculate dimensions for rasterization."""
    minx, miny, maxx, maxy = hex_gdf.total_bounds
    width = int((maxx - minx) / RASTER_RESOLUTION) + 1  # Number of pixels horizontally
    height = int((maxy - miny) / RASTER_RESOLUTION) + 1  # Number of pixels vertically
    
    # Calculate geographic aspect ratio
    geographic_aspect_ratio = (maxx - minx) / (maxy - miny)
    
    # Create transform for rasterization
    transform = Affine.translation(minx, miny) * Affine.scale(RASTER_RESOLUTION, RASTER_RESOLUTION)
    
    return minx, miny, maxx, maxy, width, height, geographic_aspect_ratio, transform


def create_buffered_geometries(hex_gdf):
    """Create buffered geometries for hexagons."""
    # Create a copy of the geometries to avoid modifying the original
    buffered_geometries = []
    buffered_values = []
    
    # Get all hexagon geometries as a single geometry for intersection testing
    all_hexagons = unary_union(hex_gdf.geometry)
    
    # Process each hexagon
    for idx, (geom, value) in enumerate(zip(hex_gdf.geometry, hex_gdf.combined_height)):
        # Add buffer to the hexagon
        buffered_geom = geom.buffer(HEXAGON_BUFFER)
        
        # Check if the buffered geometry intersects with any other hexagon
        # We need to exclude the current hexagon from the intersection test
        other_hexagons = all_hexagons.difference(geom)
        
        # If there's an intersection, we need to remove the overlapping parts
        if buffered_geom.intersects(other_hexagons):
            # Get the parts of the buffer that don't intersect with other hexagons
            valid_buffer = buffered_geom.difference(other_hexagons)
            # Combine the original hexagon with the valid buffer parts
            final_geom = geom.union(valid_buffer)
        else:
            # If no intersection, use the buffered geometry as is
            final_geom = buffered_geom
        
        buffered_geometries.append(final_geom)
        buffered_values.append(value)
    
    # Create a separate list for the buffer-only geometries (with value 0)
    buffer_only_geometries = []
    
    # For each hexagon, create a buffer-only geometry (the buffer minus the original hexagon)
    for geom, buffered_geom in zip(hex_gdf.geometry, buffered_geometries):
        # Get just the buffer part (the difference between buffered and original)
        buffer_only = buffered_geom.difference(geom)
        if not buffer_only.is_empty:
            buffer_only_geometries.append(buffer_only)
    
    return buffered_geometries, buffered_values, buffer_only_geometries


def rasterize_data(hex_gdf, buffered_geometries, buffer_only_geometries, width, height, transform):
    """Rasterize the hexagon data."""
    # Combined heights raster (terrain + features) with buffers
    # First rasterize the original hexagons
    height_mat = rasterize(
        [(geom, value) for geom, value in zip(hex_gdf.geometry, hex_gdf.combined_height)],
        out_shape=(height, width),
        transform=transform,
        fill=np.nan,
        dtype="float32"
    )
    
    # Then rasterize the buffer-only geometries with value 0
    buffer_mat = rasterize(
        [(geom, 0) for geom in buffer_only_geometries],
        out_shape=(height, width),
        transform=transform,
        fill=np.nan,
        dtype="float32"
    )
    
    # Combine the two rasters, with buffer values taking precedence where they exist
    # but preserving the original hexagon values
    buffer_mask = ~np.isnan(buffer_mat)
    height_mat[buffer_mask] = buffer_mat[buffer_mask]
    
    return height_mat


def normalize_and_colorize(height_mat, cmap):
    """Normalize height values and convert to RGB colors."""
    # Normalize height values for color mapping
    min_val = np.nanmin(height_mat)
    max_val = np.nanmax(height_mat)
    
    # Handle case where all values are the same
    if max_val == min_val:
        # If all values are the same, set normalized values to 0.5 (middle of range)
        normalized_height = np.ones_like(height_mat) * 0.5
    else:
        normalized_height = (height_mat - min_val) / (max_val - min_val)
    
    # Convert normalized values to RGB colors using the colormap
    # NaN values will be handled by the colormap
    color_mat = cmap(normalized_height)[:, :, :3]  # Take only RGB channels, drop alpha
    
    # Set the background color for NaN values and zero heights
    zero_height_mask = np.isnan(height_mat) | (height_mat == 0)
    color_mat[zero_height_mask] = BACKGROUND_RGB
    
    return color_mat


def export_to_hdf5(height_mat, color_mat, geographic_aspect_ratio):
    """Export data to HDF5 file."""
    with h5py.File(COLORIZED_H5, "w") as f:
        f.create_dataset("height", data=height_mat)
        f.create_dataset("red", data=color_mat[:,:,0])
        f.create_dataset("green", data=color_mat[:,:,1])
        f.create_dataset("blue", data=color_mat[:,:,2])
    
        # Store geographic bounds and aspect ratio as attributes
        f.attrs["aspect_ratio"] = geographic_aspect_ratio


def create_visualization(hex_gdf, height_mat, color_mat, minx, miny, maxx, maxy, geographic_aspect_ratio, transform, width, height, cmap):
    """Create and save visualization plots."""
    # Create 3 side-by-side plots: terrain, features, blended
    _, axes = plt.subplots(1, 3, figsize=(12, 6))

    # Base terrain from Perlin noise
    terrain_mat = rasterize(
        [(geom, value) for geom, value in zip(hex_gdf.geometry, hex_gdf["terrain_height"])],
        out_shape=(height, width),
        transform=transform,
        fill=np.nan,
        dtype="float32"
    )
    terrain_color_mat = cmap(terrain_mat)[:, :, :3]
    # Set background color for NaN values
    terrain_color_mat[np.isnan(terrain_mat)] = BACKGROUND_RGB

    # Feature heights
    feature_mat = rasterize(
        [(geom, value) for geom, value in zip(hex_gdf.geometry, hex_gdf["feature_height"])],
        out_shape=(height, width),
        transform=transform,
        fill=np.nan,
        dtype="float32"
    )
    min_val = np.nanmin(feature_mat)
    max_val = np.nanmax(feature_mat)
    
    # Handle case where all values are the same
    if max_val == min_val:
        # If all values are the same, set normalized values to 0.5 (middle of range)
        normalized_feature_mat = np.ones_like(feature_mat) * 0.5
    else:
        normalized_feature_mat = (feature_mat - min_val) / (max_val - min_val)
    
    feature_color_mat = cmap(normalized_feature_mat)[:, :, :3]
    
    # Set background color for NaN values
    feature_color_mat[np.isnan(feature_mat)] = BACKGROUND_RGB
    
    # Plot 1: Terrain only
    ax1 = axes[0]
    ax1.imshow(terrain_color_mat, origin='lower', extent=[minx, maxx, miny, maxy], aspect=geographic_aspect_ratio)
    ax1.set_title("Terrain")
    ax1.axis("off")

    # Plot 2: Features only
    ax2 = axes[1]
    ax2.imshow(feature_color_mat, origin='lower', extent=[minx, maxx, miny, maxy], aspect=geographic_aspect_ratio)
    ax2.set_title("Features")
    ax2.axis("off")

    # Plot 3: Final blended result
    ax3 = axes[2]
    ax3.imshow(color_mat, origin='lower', extent=[minx, maxx, miny, maxy], aspect=geographic_aspect_ratio)
    ax3.set_title("Combined")
    ax3.axis("off")

    # Save plot instead of showing it
    plt.tight_layout()
    plt.savefig(COLORIZED_PLOT, dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()


def colorize(hexagonized_data=None):
    """Main function to colorize hexagonized data."""
    # Start timing
    start_time = time.perf_counter()
    step_start = time.perf_counter()
    
    # Get the colormap
    cmap = get_colormap()
    
    # Plot the colormap
    plot_colormap(cmap)
    
    # Load hexagon data
    hex_gdf = load_hexagon_data(hexagonized_data)
    if hex_gdf is None:
        return
    
    # Calculate raster dimensions
    minx, miny, maxx, maxy, width, height, geographic_aspect_ratio, transform = calculate_raster_dimensions(hex_gdf)
    
    # Create buffered geometries
    buffered_geometries, buffered_values, buffer_only_geometries = create_buffered_geometries(hex_gdf)
    
    # Rasterize data
    height_mat = rasterize_data(hex_gdf, buffered_geometries, buffer_only_geometries, width, height, transform)
    
    # Normalize and colorize
    color_mat = normalize_and_colorize(height_mat, cmap)
    
    print_elapsed_time(step_start, "Rasterized data")
    step_start = time.perf_counter()
    
    # Print dimensions of all matrices for debugging
    logger.info(f"Height matrix: {height_mat.shape}")
    logger.info(f"Color matrix: {color_mat.shape}")
    logger.info(f"Geographic aspect ratio: {geographic_aspect_ratio}")
    
    # Export to HDF5
    export_to_hdf5(height_mat, color_mat, geographic_aspect_ratio)
    
    print_elapsed_time(step_start, "Exported to HDF5")
    
    # Create visualization if requested
    if COLORIZED_PLOT:
        step_start = time.perf_counter()
        create_visualization(hex_gdf, height_mat, color_mat, minx, miny, maxx, maxy, 
                            geographic_aspect_ratio, transform, width, height, cmap)
        print_elapsed_time(step_start, "Created visualization")
    
    return height_mat, color_mat


if __name__ == "__main__":
    colorize() 