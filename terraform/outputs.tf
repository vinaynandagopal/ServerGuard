output "public_ip" {
  value = aws_instance.serverguard.public_ip
}

output "ssh_command" {
  value = "ssh -i ${pathexpand(var.private_key_path)} ubuntu@${aws_instance.serverguard.public_ip}"
}

output "dashboard_tunnel_command" {
  description = "Run this, then open http://localhost:8080"
  value       = "ssh -i ${pathexpand(var.private_key_path)} -N -L 8080:127.0.0.1:8080 ubuntu@${aws_instance.serverguard.public_ip}"
}
