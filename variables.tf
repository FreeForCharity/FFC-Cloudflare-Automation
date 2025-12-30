variable "cloudflare_api_token" {
  description = "CloudFlare API token with DNS edit permissions"
  type        = string
  sensitive   = true
}

variable "domain_name" {
  description = "The domain name managed by CloudFlare (e.g., example.com)"
  type        = string
}

variable "github_pages_domain" {
  description = "The GitHub Pages domain (e.g., username.github.io or org.github.io)"
  type        = string
}

variable "github_pages_ips" {
  description = "List of GitHub Pages IP addresses for A records"
  type        = list(string)
  default = [
    "185.199.108.153",
    "185.199.109.153",
    "185.199.110.153",
    "185.199.111.153"
  ]
}

variable "dns_ttl" {
  description = "TTL for DNS records when not proxied (ignored when proxied=true, which requires TTL=1)"
  type        = number
  default     = 1
}

variable "proxied" {
  description = "Whether the DNS records should be proxied through CloudFlare"
  type        = bool
  default     = false
}

variable "create_www_record" {
  description = "Whether to create a www CNAME record"
  type        = bool
  default     = true
}

variable "create_caa_records" {
  description = "Whether to create CAA records for SSL certificate management"
  type        = bool
  default     = false
}

variable "caa_records" {
  description = "List of CAA records to create"
  type = list(object({
    flags = string
    tag   = string
    value = string
  }))
  default = [
    {
      flags = "0"
      tag   = "issue"
      value = "letsencrypt.org"
    }
  ]
}

variable "create_https_redirect" {
  description = "Whether to create a page rule for HTTPS redirect"
  type        = bool
  default     = true
}

variable "configure_ssl_settings" {
  description = "Whether to configure SSL/TLS settings"
  type        = bool
  default     = true
}

variable "ssl_mode" {
  description = "SSL mode (off, flexible, full, strict)"
  type        = string
  default     = "full"

  validation {
    condition     = contains(["off", "flexible", "full", "strict"], var.ssl_mode)
    error_message = "SSL mode must be one of: off, flexible, full, strict"
  }
}

variable "always_use_https" {
  description = "Whether to always use HTTPS"
  type        = bool
  default     = true
}

variable "automatic_https_rewrites" {
  description = "Whether to enable automatic HTTPS rewrites"
  type        = bool
  default     = true
}

variable "min_tls_version" {
  description = "Minimum TLS version (1.0, 1.1, 1.2, 1.3)"
  type        = string
  default     = "1.2"

  validation {
    condition     = contains(["1.0", "1.1", "1.2", "1.3"], var.min_tls_version)
    error_message = "Minimum TLS version must be one of: 1.0, 1.1, 1.2, 1.3"
  }
}
