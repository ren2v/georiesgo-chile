output "grupo_recursos" {
  value = azurerm_resource_group.principal.name
}

output "url_api" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "nombre_container_app" {
  value = azurerm_container_app.api.name
}

output "registro" {
  description = "Servidor del Container Registry (para docker push)."
  value       = azurerm_container_registry.registro.login_server
}

output "url_frontend" {
  value = "https://${azurerm_static_web_app.frontend.default_host_name}"
}

output "token_despliegue_frontend" {
  description = "Token para publicar el frontend en Static Web Apps (lo usa GitHub Actions)."
  value       = azurerm_static_web_app.frontend.api_key
  sensitive   = true
}

output "database_url" {
  description = "Cadena de conexión para correr el ETL contra la base de Azure."
  value       = local.database_url
  sensitive   = true
}
