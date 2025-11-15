# Configuration with strict SSL/TLS settings
# Maximum security configuration

cloudflare_api_token = "your-api-token-here"
domain_name          = "example.com"
github_pages_domain  = "username.github.io"

# Standard DNS settings
create_www_record = true
dns_ttl           = 1
proxied           = false

# Strict SSL/TLS configuration
configure_ssl_settings   = true
ssl_mode                 = "strict" # Strict mode for maximum security
always_use_https         = true
automatic_https_rewrites = true
min_tls_version          = "1.3" # Require TLS 1.3

# Force HTTPS
create_https_redirect = true

# Optional: Add CAA records for certificate authority
create_caa_records = true
caa_records = [
  {
    flags = "0"
    tag   = "issue"
    value = "letsencrypt.org"
  },
  {
    flags = "0"
    tag   = "issuewild"
    value = "letsencrypt.org"
  }
]
