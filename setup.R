# Set CRAN repository to avoid interactive prompts
options(repos = c(CRAN = "https://cloud.r-project.org"))

# Install BiocManager if not already installed
if (!requireNamespace("BiocManager", quietly = TRUE))
    install.packages("BiocManager")

BiocManager::install(c("rhdf5", "rayshader", "rayrender", "rgl", "dotenv"), force = TRUE, update = TRUE, ask = FALSE)
