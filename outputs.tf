output "zone_id" {
  description = "The CloudFlare zone ID for the domain"
  value       = data.cloudflare_zone.domain.id
}

output "zone_name" {
  description = "The domain name"
  value       = data.cloudflare_zone.domain.name
}

output "github_pages_a_records" {
  description = "The GitHub Pages A records created"
  value = [
    for record in cloudflare_record.github_pages_a : {
      name    = record.name
      content = record.content
      type    = record.type
      id      = record.id
    }
  ]
}

output "github_pages_www_record" {
  description = "The GitHub Pages www CNAME record created"
  value = var.create_www_record ? {
    name    = cloudflare_record.github_pages_www[0].name
    content = cloudflare_record.github_pages_www[0].content
    type    = cloudflare_record.github_pages_www[0].type
    id      = cloudflare_record.github_pages_www[0].id
  } : null
}

output "nameservers" {
  description = "CloudFlare nameservers for the domain"
  value       = data.cloudflare_zone.domain.name_servers
}

output "status" {
  description = "CloudFlare zone status"
  value       = data.cloudflare_zone.domain.status
}
