# Group C — DNS (hidden-view.com hosted zone).
#
# Deliberately NOT modeled: the zone's NS and SOA record sets. They're AWS-managed
# defaults tied 1:1 to the zone itself — the zone resource already exposes the
# nameservers via its own `name_servers` computed attribute, and importing NS/SOA as
# separate aws_route53_record resources is a well-known conflict trap. Only the two
# real application records (apex + www) are modeled.
resource "aws_route53_zone" "hidden_view" {
  name    = "hidden-view.com"
  comment = "HostedZone created by Route53 Registrar"
}

resource "aws_route53_record" "apex_a" {
  zone_id = aws_route53_zone.hidden_view.zone_id
  name    = "hidden-view.com"
  type    = "A"
  ttl     = 300
  records = ["3.86.118.17"]
}

resource "aws_route53_record" "www_a" {
  zone_id = aws_route53_zone.hidden_view.zone_id
  name    = "www.hidden-view.com"
  type    = "A"
  ttl     = 300
  records = ["3.86.118.17"]
}
