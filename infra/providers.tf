terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Estado remoto en Azure Storage (compartido entre la máquina local y
  # GitHub Actions). Los datos de la cuenta van en backend.hcl, que crea el
  # script infra/bootstrap-estado.ps1; así este archivo no depende de una
  # suscripción en particular.
  backend "azurerm" {}
}

provider "azurerm" {
  features {
    resource_group {
      # Permite destruir el grupo aunque Azure haya creado recursos por su
      # cuenta dentro (p. ej. alertas automáticas).
      prevent_deletion_if_contains_resources = false
    }
  }
  subscription_id = var.subscription_id
}
