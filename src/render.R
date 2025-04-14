library(rayshader)
library(rhdf5)
library(rgl)
library(dotenv)
library(rayrender)


load_dot_env()

# === Configurable Parameters ===
# Function to get environment variables with defaults
get_env_var <- function(name, default, convert_type = "character") {
  value <- Sys.getenv(name, default)
  if (convert_type == "numeric") {
    return(as.numeric(value))
  } else if (convert_type == "numeric_vector") {
    return(as.numeric(strsplit(value, ",")[[1]]))
  } else if (convert_type == "character_vector") {
    return(strsplit(value, ",")[[1]])
  }
  return(value)
}

# Input/Output paths
h5_path <- get_env_var("COLORIZED_H5", "output/matrices.h5")
render_path <- get_env_var("RENDER_PATH", "output/rendering.png")
background_color <- get_env_var("BACKGROUND_COLOR", "255,255,255", "numeric_vector")

# Height matrix parameters
height_multiplier <- get_env_var("HEIGHT_MULTIPLIER", "60", "numeric")
zscale <- get_env_var("ZSCALE", "1", "numeric")

# Camera parameters
zoom <- get_env_var("ZOOM", "1.2", "numeric")
theta <- get_env_var("THETA", "270", "numeric")
phi <- get_env_var("PHI", "60", "numeric")

# Render parameters
light_direction <- get_env_var("LIGHT_DIRECTION", "250", "numeric")
light_altitude <- get_env_var("LIGHT_ALTITUDE", "30,80", "numeric_vector")
light_color <- get_env_var("LIGHT_COLOR", "#90D5FF,white", "character_vector")
light_intensity <- get_env_var("LIGHT_INTENSITY", "600,100", "numeric_vector")
samples <- get_env_var("SAMPLES", "1024", "numeric")
height <- get_env_var("HEIGHT", "512", "numeric")
width <- get_env_var("WIDTH", "512", "numeric")

# === Load from HDF5 ===
height_matrix <- h5read(h5_path, "height")
red_channel   <- h5read(h5_path, "red")
green_channel <- h5read(h5_path, "green")
blue_channel  <- h5read(h5_path, "blue")

# Convert background color from RGB (0-255) to hex
background_hex <- sprintf("#%02X%02X%02X", background_color[1], background_color[2], background_color[3])
cat("Background color:", background_hex, "\n")

# === Orient height matrix and apply multiplier ===
height_matrix <- t(height_matrix)
height_matrix[!is.nan(height_matrix)] <- height_matrix[!is.nan(height_matrix)] * height_multiplier

# Combine into RGB array
rgb_array <- array(0, dim = c(nrow(red_channel), ncol(red_channel), 3))
rgb_array[,,1] <- red_channel
rgb_array[,,2] <- green_channel
rgb_array[,,3] <- blue_channel

# === Load and apply aspect ratio ===
h5_attrs <- h5readAttributes(h5_path, "/")
inverse_aspect_ratio <- 1 / h5_attrs$aspect_ratio

# === 3D Plot ===
rgl::close3d()
rgl::open3d()

start_time <- Sys.time()
cat("Render started at:", start_time, "\n")

# https://www.rayshader.com/reference/plot_3d.html
plot_3d(
  heightmap = height_matrix,
  hillshade = rgb_array,
  zscale = zscale,
  asp = inverse_aspect_ratio,
  background = background_hex,
  solid = FALSE,
  shadowdepth = 0
)

# Adjust camera
# https://www.rayshader.com/reference/render_camera.html
render_camera(
  zoom = zoom,
  theta = theta,
  phi = phi
)

# High-quality render
# https://www.rayshader.com/reference/render_highquality.html
# https://www.rayrender.net/reference/render_scene.html
render_highquality(
  filename = render_path,
  interactive = FALSE,
  sample_method = "sobol",
  samples = samples,
  height = height,
  width = width,
  light = TRUE,
  lightdirection = light_direction,
  lightaltitude = light_altitude,
  lightcolor = light_color,
  lightintensity = light_intensity,
  ground_material = rayrender::diffuse(color = background_hex),
)

rgl::close3d()

end_time <- Sys.time()
cat("Render completed in:", end_time - start_time, "\n")
