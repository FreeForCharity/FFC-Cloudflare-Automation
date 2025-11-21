provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# Get the zone ID for the domain
data "cloudflare_zone" "domain" {
  name = var.domain_name
}

# Create A records pointing to GitHub Pages IP addresses
resource "cloudflare_record" "github_pages_a" {
  count = length(var.github_pages_ips)

  zone_id = data.cloudflare_zone.domain.id
  name    = "@"
  content = var.github_pages_ips[count.index]
  type    = "A"
  ttl     = var.proxied ? 1 : var.dns_ttl
  proxied = var.proxied

  comment = "GitHub Pages A record ${count.index + 1}"
}

# Create CNAME record for www subdomain
resource "cloudflare_record" "github_pages_www" {
  count = var.create_www_record ? 1 : 0

  zone_id = data.cloudflare_zone.domain.id
  name    = "www"
  content = var.github_pages_domain
  type    = "CNAME"
  ttl     = var.proxied ? 1 : var.dns_ttl
  proxied = var.proxied

  comment = "GitHub Pages www CNAME record"
}

# Optional: Create CAA records for SSL certificate management
resource "cloudflare_record" "github_pages_caa" {
  count = var.create_caa_records ? length(var.caa_records) : 0

  zone_id = data.cloudflare_zone.domain.id
  name    = "@"
  type    = "CAA"
  ttl     = var.proxied ? 1 : var.dns_ttl

  content = "${var.caa_records[count.index].flags} ${var.caa_records[count.index].tag} \"${var.caa_records[count.index].value}\""

  data {
    flags = var.caa_records[count.index].flags
    tag   = var.caa_records[count.index].tag
    value = var.caa_records[count.index].value
  }

  comment = "CAA record for SSL certificate management"
}

# Configure CloudFlare page rules for GitHub Pages (optional)
resource "cloudflare_page_rule" "github_pages_https" {
  count = var.create_https_redirect ? 1 : 0

  zone_id  = data.cloudflare_zone.domain.id
  target   = "http://${var.domain_name}/*"
  priority = 1

  actions {
    always_use_https = true
  }
}

# Configure CloudFlare SSL settings
resource "cloudflare_zone_settings_override" "github_pages_ssl" {
  count = var.configure_ssl_settings ? 1 : 0

  zone_id = data.cloudflare_zone.domain.id

  settings {
    ssl                      = var.ssl_mode
    always_use_https         = var.always_use_https ? "on" : "off"
    automatic_https_rewrites = var.automatic_https_rewrites ? "on" : "off"
    min_tls_version          = var.min_tls_version
  }
}
