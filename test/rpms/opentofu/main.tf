terraform {
  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
  }
}

resource "local_file" "test" {
  filename = "/tmp/opentofu-test-output.txt"
  content  = "opentofu works\n"
}
