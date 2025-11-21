# Configuration for apex domain only (no www subdomain)
# Use this if you only want example.com and not www.example.com

cloudflare_api_token = "your-api-token-here"
domain_name          = "example.com"
github_pages_domain  = "username.github.io"

# Disable www subdomain
create_www_record = false

# Standard settings
dns_ttl               = 1
proxied               = false
create_https_redirect = true

# SSL/TLS settings
configure_ssl_settings   = true
ssl_mode                 = "full"
always_use_https         = true
automatic_https_rewrites = true
min_tls_version          = "1.2"
