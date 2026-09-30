# Infraestructura de GeoRiesgo Chile en Azure:
#
#   Static Web App (frontend) ──> Container App (API + agente) ──> PostgreSQL
#                                        │                        (PostGIS + pgvector)
#                                        └── imagen pública en GitHub Container Registry
#   Logs de la API ──> Log Analytics
#
# Diseñado para costar US$0 en una suscripción Azure for Students:
# - PostgreSQL B1ms con 32 GB: incluido gratis 12 meses (750 h/mes).
# - Container Apps: escala a cero; el uso activo cae en la cuota gratuita
#   mensual (180.000 vCPU-s).
# - Static Web Apps: plan gratis.
# - Imagen en GHCR (gratis en repos públicos) en vez de Azure Container
#   Registry, que cuesta después de los 12 meses.
# - Log Analytics con tope diario bajo la cuota gratuita de 5 GB/mes.
# Pasados los 12 meses la base empieza a consumir crédito: `terraform destroy`.

resource "random_string" "sufijo" {
  # Algunos nombres (registro, base, storage) son globales en Azure.
  length  = 5
  upper   = false
  special = false
}

locals {
  nombre = "${var.prefijo}-${random_string.sufijo.result}"
}

resource "azurerm_resource_group" "principal" {
  name     = "rg-${var.prefijo}"
  location = var.ubicacion
  tags     = var.etiquetas
}

# --- Observabilidad ---------------------------------------------------------

resource "azurerm_log_analytics_workspace" "logs" {
  name                = "log-${local.nombre}"
  resource_group_name = azurerm_resource_group.principal.name
  location            = azurerm_resource_group.principal.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  # Tope diario de ingesta: 0,15 GB/día x 30 = 4,5 GB/mes, bajo los 5 GB
  # mensuales gratuitos de Log Analytics.
  daily_quota_gb = 0.15
  tags           = var.etiquetas
}

# --- Base de datos ----------------------------------------------------------

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "db" {
  name                   = "psql-${local.nombre}"
  resource_group_name    = azurerm_resource_group.principal.name
  location               = azurerm_resource_group.principal.location
  version                = "16"
  sku_name               = "B_Standard_B1ms"
  storage_mb             = 32768
  administrator_login    = "georiesgo"
  administrator_password = random_password.db.result
  backup_retention_days  = 7
  # Sin zona fija: Azure elige una con capacidad (fijarla hace fallar el
  # apply en regiones con poca disponibilidad).
  zone = null
  tags = var.etiquetas

  lifecycle {
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "georiesgo" {
  name      = "georiesgo"
  server_id = azurerm_postgresql_flexible_server.db.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# En Azure las extensiones deben estar en la lista permitida del servidor
# antes de poder hacer CREATE EXTENSION.
resource "azurerm_postgresql_flexible_server_configuration" "extensiones" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.db.id
  value     = "POSTGIS,VECTOR"
}

# 0.0.0.0 es la regla especial de Azure para "servicios de Azure" (incluye
# Container Apps). Una red privada sería más estricta, pero exige un plan de
# Container Apps con VNet y cuesta más: se deja anotado como mejora.
resource "azurerm_postgresql_flexible_server_firewall_rule" "servicios_azure" {
  name             = "servicios-azure"
  server_id        = azurerm_postgresql_flexible_server.db.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "administrador" {
  count            = var.ip_administrador == "" ? 0 : 1
  name             = "administrador"
  server_id        = azurerm_postgresql_flexible_server.db.id
  start_ip_address = var.ip_administrador
  end_ip_address   = var.ip_administrador
}

locals {
  database_url = format(
    "postgresql+psycopg2://%s:%s@%s:5432/%s?sslmode=require",
    azurerm_postgresql_flexible_server.db.administrator_login,
    random_password.db.result,
    azurerm_postgresql_flexible_server.db.fqdn,
    azurerm_postgresql_flexible_server_database.georiesgo.name,
  )
}

# --- API --------------------------------------------------------------------

resource "azurerm_container_app_environment" "principal" {
  name                       = "cae-${local.nombre}"
  resource_group_name        = azurerm_resource_group.principal.name
  location                   = azurerm_resource_group.principal.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.logs.id
  tags                       = var.etiquetas
}

resource "azurerm_container_app" "api" {
  name                         = "ca-${var.prefijo}-api"
  resource_group_name          = azurerm_resource_group.principal.name
  container_app_environment_id = azurerm_container_app_environment.principal.id
  revision_mode                = "Single"
  tags                         = var.etiquetas

  # La imagen es pública en GitHub Container Registry, así que no hace falta
  # un bloque `registry` con credenciales.

  # Secretos de Container Apps: no quedan en texto plano en la definición
  # del contenedor ni en los logs.
  secret {
    name  = "database-url"
    value = local.database_url
  }

  secret {
    name = "google-api-key"
    # Container Apps no acepta secretos vacíos.
    value = var.google_api_key == "" ? "sin-configurar" : var.google_api_key
  }

  ingress {
    external_enabled = true
    target_port      = var.puerto_api
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    # Escala a cero sin tráfico: solo se paga (o se consume de la cuota
    # gratuita) mientras hay requests. Máximo 1 réplica porque la memoria de
    # conversación del agente vive en el proceso.
    min_replicas = 0
    max_replicas = 1

    container {
      name   = "api"
      image  = var.imagen_api
      cpu    = 1.0
      memory = "2Gi"

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "GOOGLE_API_KEY"
        secret_name = "google-api-key"
      }
      env {
        name  = "GEORIESGO_BACKEND"
        value = "postgis"
      }
    }
  }

  lifecycle {
    # Después del primer apply, la imagen la actualiza el pipeline de CI/CD
    # (az containerapp update); Terraform no debe revertirla a la de ejemplo.
    ignore_changes = [template[0].container[0].image, ingress[0].target_port]
  }
}

# --- Frontend ---------------------------------------------------------------

resource "azurerm_static_web_app" "frontend" {
  name                = "stapp-${local.nombre}"
  resource_group_name = azurerm_resource_group.principal.name
  # Static Web Apps solo existe en algunas regiones; no afecta la latencia
  # porque el contenido se sirve desde una red de distribución global.
  location = "eastus2"
  sku_tier = "Free"
  sku_size = "Free"
  tags     = var.etiquetas
}
