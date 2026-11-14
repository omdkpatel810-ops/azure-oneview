# Shared stand-ins for Entra ID, used by every test file.

mock_resource "azuread_application" {
  defaults = {
    id        = "/applications/00000000-0000-0000-0000-00000000dddd"
    client_id = "00000000-0000-0000-0000-00000000eeee"
  }
}

mock_resource "azuread_service_principal" {
  defaults = { object_id = "00000000-0000-0000-0000-00000000ffff" }
}
