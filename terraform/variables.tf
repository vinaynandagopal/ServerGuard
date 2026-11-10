variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "instance_type" {
  type    = string
  default = "t3.micro"
}

variable "my_ip_cidr" {
  type        = string
  description = "Your public IP as a /32, e.g. 203.0.113.7/32. SSH is allowed from here only."

  validation {
    condition     = can(cidrhost(var.my_ip_cidr, 0)) && endswith(var.my_ip_cidr, "/32")
    error_message = "my_ip_cidr must be a single IPv4 address in /32 form (never 0.0.0.0/0)."
  }
}

variable "private_key_path" {
  type    = string
  default = "~/.ssh/serverguard_aws"
}
