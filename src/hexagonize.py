from dotenv import load_dotenv
import h3
import geopandas as gpd
from shapely.geometry import mapping, Polygon
from perlin_noise import PerlinNoise
import time
from loguru import logger
import pickle
import matplotlib.pyplot as plt
import numpy as np
from utils import get_env_var, print_elapsed_time

# Load environment variables from .env file
load_dotenv()

# ===== Configuration Parameters =====
# Base Parameters
TERRAIN_FILE = get_env_var("TERRAIN_FILE")
TERRAIN_LAYER = get_env_var("TERRAIN_LAYER")
FEATURE_GPKG = get_env_var("FEATURE_GPKG")
FEATURE_COLUMN = get_env_var("FEATURE_COLUMN")

AGGREGATION_METHOD = get_env_var("AGGREGATION_METHOD", "max")
HEXAGONS_PICKLE = get_env_var("HEXAGONS_PICKLE", "output/hexagons.pkl")
HEXAGONS_PLOT = get_env_var("HEXAGONS_PLOT", "output/hexagons.png")

# Filter Parameters
FILTER_COLUMN = get_env_var("FILTER_COLUMN")
FILTER_CONDITION = get_env_var("FILTER_CONDITION")

# H3 Parameters
H3_RESOLUTION = get_env_var("H3_RESOLUTION", 6, int)

# Perlin Noise Parameters
USE_PERLIN = get_env_var("USE_PERLIN", "True", bool)
NOISE_OCTAVES = get_env_var("NOISE_OCTAVES", 6, int)
NOISE_VARIATION = get_env_var("NOISE_VARIATION", 2, float)
NOISE_SCALE = get_env_var("NOISE_SCALE", 0.15, float)


# ===== Utility Functions =====
def h3_cell_to_polygon(h3_cell):
    """Convert H3 cell to Shapely polygon."""
    boundary = h3.cell_to_boundary(h3_cell)
    return Polygon([(lng, lat) for lat, lng in boundary])


def load_terrain_data():
    """Load and filter terrain data from file."""
    # Check if the file is a GeoPackage (ends with .gpkg)
    if TERRAIN_FILE.lower().endswith(".gpkg"):
        # Load from GeoPackage with layer
        terrain = gpd.read_file(TERRAIN_FILE, layer=TERRAIN_LAYER).to_crs(epsg=4326)
    else:
        # Load from shapefile or other format
        terrain = gpd.read_file(TERRAIN_FILE).to_crs(epsg=4326)

    # Apply filter if specified
    if FILTER_COLUMN and FILTER_CONDITION:
        terrain = terrain[terrain[FILTER_COLUMN] == FILTER_CONDITION]

    return terrain


def generate_hex_grid(terrain_boundary):
    """Generate hexagon grid with optional Perlin noise for terrain height."""
    boundary_geojson = mapping(terrain_boundary)
    h3_shape = h3.geo_to_h3shape(boundary_geojson)
    h3_hexes = h3.polygon_to_cells(h3_shape, H3_RESOLUTION)

    logger.info(f"Number of hexagons: {len(h3_hexes)}")

    # Optionally initialize Perlin
    noise = PerlinNoise(octaves=NOISE_OCTAVES, seed=1337) if USE_PERLIN else None

    # Generate hex grid
    hex_data = []
    for h3_cell in h3_hexes:
        polygon = h3_cell_to_polygon(h3_cell)
        centroid = polygon.centroid

        # Scale Perlin noise to match feature height range
        if USE_PERLIN:
            # Perlin noise is in [-1,1]
            raw_noise = noise(
                [centroid.x / NOISE_VARIATION, centroid.y / NOISE_VARIATION]
            )

            # Normalize noise to [0,1] and scale to feature range
            normalized_noise = (raw_noise + 1) / 2

            # Scale noise directly by noise_scale to get values between 0 and noise_scale
            terrain_height = normalized_noise * NOISE_SCALE
        else:
            terrain_height = 0.0

        hex_data.append({"geometry": polygon, "terrain_height": terrain_height})

    return gpd.GeoDataFrame(hex_data, crs="EPSG:4326"), h3_hexes


def load_and_filter_features(terrain_boundary):
    """Load and filter features from GeoPackage."""
    features = gpd.read_file(FEATURE_GPKG).to_crs("EPSG:4326")
    features = features[features.geometry.within(terrain_boundary)]
    return features


def aggregate_features(hex_gdf, features):
    """Join features to hexagons and aggregate values."""
    joined = gpd.sjoin(hex_gdf, features, predicate="intersects", how="left")

    if AGGREGATION_METHOD == "sum":
        hex_gdf["feature_height"] = (
            joined.groupby(joined.index)[FEATURE_COLUMN].sum().fillna(0)
        )
    elif AGGREGATION_METHOD == "mean":
        hex_gdf["feature_height"] = (
            joined.groupby(joined.index)[FEATURE_COLUMN].mean().fillna(0)
        )
    elif AGGREGATION_METHOD == "max":
        hex_gdf["feature_height"] = (
            joined.groupby(joined.index)[FEATURE_COLUMN].max().fillna(0)
        )
    elif AGGREGATION_METHOD == "min":
        hex_gdf["feature_height"] = (
            joined.groupby(joined.index)[FEATURE_COLUMN].min().fillna(0)
        )

    return hex_gdf


def normalize_feature_heights(hex_gdf):
    """Normalize feature heights to 0.01-1 range."""
    non_zero_mask = hex_gdf["feature_height"] > 0
    non_zero_features = hex_gdf.loc[non_zero_mask, "feature_height"]

    max_feature_height = non_zero_features.max()
    min_feature_height = non_zero_features.min()
    feature_range = max_feature_height - min_feature_height

    # Scale non-zero values to 0.01-1 range
    # Convert to float64 to avoid dtype incompatibility
    hex_gdf["feature_height"] = hex_gdf["feature_height"].astype(np.float64)
    hex_gdf.loc[non_zero_mask, "feature_height"] = (
        0.01 + 0.99 * (non_zero_features - min_feature_height) / feature_range
    )

    return hex_gdf


def combine_heights(hex_gdf):
    """Combine terrain and feature heights."""
    hex_gdf["combined_height"] = hex_gdf["terrain_height"] + hex_gdf["feature_height"]
    return hex_gdf


def save_hexagon_data(hex_gdf):
    """Save hexagonized data to pickle file."""
    with open(HEXAGONS_PICKLE, "wb") as f:
        pickle.dump(hex_gdf, f)


def create_hexagon_visualization(hex_gdf):
    """Create and save visualization of hexagons."""
    # Create a figure with 3 subplots: terrain, features, and combined
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    # Find the global min and max for consistent color scaling
    vmin = min(
        hex_gdf["terrain_height"].min(),
        hex_gdf["feature_height"].min(),
        hex_gdf["combined_height"].min(),
    )
    vmax = max(
        hex_gdf["terrain_height"].max(),
        hex_gdf["feature_height"].max(),
        hex_gdf["combined_height"].max(),
    )

    # Calculate the aspect ratio based on the bounds of the data
    bounds = hex_gdf.total_bounds
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    aspect_ratio = width / height

    colormap = "Spectral_r"

    # Plot 1: Terrain heights
    ax1 = axes[0]
    hex_gdf.plot(column="terrain_height", ax=ax1, cmap=colormap, vmin=vmin, vmax=vmax)
    ax1.set_title("Terrain")
    ax1.axis("off")
    ax1.set_aspect(aspect_ratio)

    # Plot 2: Feature heights
    ax2 = axes[1]
    hex_gdf.plot(column="feature_height", ax=ax2, cmap=colormap, vmin=vmin, vmax=vmax)
    ax2.set_title("Feature")
    ax2.axis("off")
    ax2.set_aspect(aspect_ratio)

    # Plot 3: Combined heights
    ax3 = axes[2]
    hex_gdf.plot(column="combined_height", ax=ax3, cmap=colormap, vmin=vmin, vmax=vmax)
    ax3.set_title("Combined")
    ax3.axis("off")
    ax3.set_aspect(aspect_ratio)

    # Add a colorbar to the right of the plots
    norm = plt.Normalize(vmin=vmin, vmax=vmax)
    sm = plt.cm.ScalarMappable(cmap=colormap, norm=norm)
    sm.set_array([])

    # Create a separate axes for the colorbar to avoid tight_layout issues
    cbar_ax = fig.add_axes([0.25, 0.05, 0.5, 0.03])
    cbar = plt.colorbar(sm, cax=cbar_ax, orientation="horizontal")
    cbar.set_label("Height")

    # Adjust layout to make room for the colorbar
    plt.subplots_adjust(bottom=0.15)
    plt.savefig(HEXAGONS_PLOT, dpi=300, bbox_inches="tight")
    plt.close()


def hexagonize():
    """Main function to hexagonize terrain and feature data."""
    # Start timing
    step_start = time.perf_counter()

    # Load terrain data
    terrain = load_terrain_data()
    terrain_boundary = terrain.geometry.union_all()

    print_elapsed_time(step_start, "Loaded terrain boundary")
    step_start = time.perf_counter()

    # Generate hex grid
    hex_gdf, _ = generate_hex_grid(terrain_boundary)

    print_elapsed_time(step_start, "Generated terrain hex grid")
    step_start = time.perf_counter()

    # Load and filter features
    features = load_and_filter_features(terrain_boundary)

    print_elapsed_time(step_start, "Loaded and filtered features")
    step_start = time.perf_counter()

    # Aggregate features
    hex_gdf = aggregate_features(hex_gdf, features)

    # Normalize feature heights
    hex_gdf = normalize_feature_heights(hex_gdf)

    # Combine heights
    hex_gdf = combine_heights(hex_gdf)

    # Save hexagon data
    save_hexagon_data(hex_gdf)

    print_elapsed_time(step_start, "Combined terrain and feature values")
    step_start = time.perf_counter()

    # Create visualization if requested
    if HEXAGONS_PLOT:
        create_hexagon_visualization(hex_gdf)
        print_elapsed_time(step_start, "Created hexagon visualization")

    return hex_gdf


if __name__ == "__main__":
    # Run hexagonization and save the result to a file
    hexagonize()
