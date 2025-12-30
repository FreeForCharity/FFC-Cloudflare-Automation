# Configuration with CloudFlare CDN enabled
# Proxies traffic through CloudFlare for better performance and DDoS protection

cloudflare_api_token = "your-api-token-here"
domain_name          = "example.com"
github_pages_domain  = "username.github.io"

# Enable CloudFlare CDN proxying
proxied = true

# CDN works best with automatic TTL (when proxied=true, TTL is automatically set to 1)
# This dns_ttl setting is ignored when proxied is enabled
dns_ttl = 1

# Standard HTTPS settings
create_www_record     = true
create_https_redirect = true

# SSL/TLS configuration for CDN
configure_ssl_settings   = true
ssl_mode                 = "full"
always_use_https         = true
automatic_https_rewrites = true
min_tls_version          = "1.2"
