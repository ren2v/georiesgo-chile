<#
Prepara lo que Terraform necesita ANTES de su primer `init`:

  1. Muestra las regiones que permite la suscripción (Azure for Students
     restringe regiones por política, y un apply en una región no permitida
     falla recién al final).
  2. Registra los proveedores de recursos que usa el proyecto (en una
     suscripción nueva vienen sin registrar).
  3. Crea un Storage Account para el estado remoto de Terraform y escribe
     backend.hcl (ignorado por git).

Uso (después de `az login`):
    .\bootstrap-estado.ps1 -Ubicacion brazilsouth
#>
param(
    [string]$Ubicacion = "brazilsouth",
    [string]$GrupoEstado = "rg-georiesgo-tfstate"
)

$ErrorActionPreference = "Stop"

$suscripcion = az account show --query "{id:id, nombre:name}" -o json | ConvertFrom-Json
Write-Host "Suscripción: $($suscripcion.nombre) ($($suscripcion.id))"

# --- 1. Regiones permitidas --------------------------------------------------
$asignaciones = az policy assignment list --query "[?contains(displayName, 'location') || contains(displayName, 'region') || contains(displayName, 'Location')]" -o json | ConvertFrom-Json
$permitidas = @()
foreach ($a in $asignaciones) {
    $valores = $a.parameters.listOfAllowedLocations.value
    if ($valores) { $permitidas += $valores }
}
if ($permitidas) {
    Write-Host "Regiones permitidas por política: $($permitidas -join ', ')"
    if ($permitidas -notcontains $Ubicacion) {
        throw "La región '$Ubicacion' no está permitida. Usa una de: $($permitidas -join ', ')"
    }
} else {
    Write-Host "Sin política de regiones visible: se usará '$Ubicacion'."
}

# --- 2. Proveedores de recursos ----------------------------------------------
$proveedores = "Microsoft.App", "Microsoft.DBforPostgreSQL", "Microsoft.OperationalInsights",
               "Microsoft.Web", "Microsoft.Storage"
foreach ($p in $proveedores) {
    $estado = az provider show --namespace $p --query registrationState -o tsv
    if ($estado -ne "Registered") {
        Write-Host "Registrando $p..."
        az provider register --namespace $p --wait | Out-Null
    }
}
Write-Host "Proveedores de recursos registrados."

# --- 3. Estado remoto ----------------------------------------------------------
az group create --name $GrupoEstado --location $Ubicacion --tags proyecto=georiesgo-chile gestionado=bootstrap -o none

# El nombre del Storage Account es global: se deriva de la suscripción para
# que sea estable (correr el script dos veces no crea otra cuenta).
$sufijo = ($suscripcion.id -replace "-", "").Substring(0, 8)
$cuenta = "stgeoriesgotf$sufijo"

az storage account create --name $cuenta --resource-group $GrupoEstado --location $Ubicacion `
    --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 `
    --allow-blob-public-access false -o none
# Versionado de blobs: si un apply corrompe el estado, se puede recuperar.
az storage account blob-service-properties update --account-name $cuenta --resource-group $GrupoEstado `
    --enable-versioning true -o none
az storage container create --name tfstate --account-name $cuenta --auth-mode login -o none

# Tu usuario necesita permiso de datos sobre el blob (con --auth-mode login
# Terraform usa tu identidad, no la clave del Storage Account).
$usuario = az ad signed-in-user show --query id -o tsv
$alcance = az storage account show --name $cuenta --resource-group $GrupoEstado --query id -o tsv
az role assignment create --assignee $usuario --role "Storage Blob Data Contributor" --scope $alcance -o none 2>$null

@"
resource_group_name  = "$GrupoEstado"
storage_account_name = "$cuenta"
container_name       = "tfstate"
key                  = "georiesgo.tfstate"
use_azuread_auth     = true
"@ | Set-Content -Path (Join-Path $PSScriptRoot "backend.hcl") -Encoding utf8

Write-Host ""
Write-Host "Listo. Siguiente paso:"
Write-Host "  terraform init -backend-config=backend.hcl"
