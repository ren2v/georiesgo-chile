variable "subscription_id" {
  description = "ID de la suscripción de Azure (az account show --query id -o tsv)."
  type        = string
}

variable "ubicacion" {
  description = "Región de Azure. Las suscripciones Azure for Students solo permiten algunas regiones: revisar con infra/bootstrap-estado.ps1."
  type        = string
  default     = "brazilsouth"
}

variable "prefijo" {
  description = "Prefijo para los nombres de los recursos."
  type        = string
  default     = "georiesgo"
}

variable "imagen_api" {
  description = "Imagen de la API (ghcr.io/<usuario>/georiesgo-chile-api:<tag>). El primer apply usa una imagen pública de ejemplo; después la reemplaza el pipeline de CI/CD."
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "puerto_api" {
  description = "Puerto en que escucha el contenedor (8000 para la API; 80 para la imagen de ejemplo)."
  type        = number
  default     = 80
}

variable "google_api_key" {
  description = "API key de Gemini para el agente. Vacía = el agente responde 503 y el resto de la API funciona."
  type        = string
  default     = ""
  sensitive   = true
}

variable "ip_administrador" {
  description = "IP pública desde la que se corre el ETL contra la base (vacía = sin acceso externo)."
  type        = string
  default     = ""
}

variable "etiquetas" {
  description = "Etiquetas aplicadas a todos los recursos."
  type        = map(string)
  default = {
    proyecto   = "georiesgo-chile"
    gestionado = "terraform"
  }
}
