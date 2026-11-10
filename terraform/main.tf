data "aws_vpc" "default" {
  default = true
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_key_pair" "serverguard" {
  key_name   = "serverguard-key"
  public_key = file(pathexpand("${var.private_key_path}.pub"))
}

resource "aws_security_group" "serverguard" {
  name        = "serverguard-sg"
  description = "SSH from admin IP only. Dashboard is never exposed."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH from admin IP only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip_cidr]
  }

  egress {
    description = "Outbound for apt, pip and git"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "serverguard" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  key_name                    = aws_key_pair.serverguard.key_name
  vpc_security_group_ids      = [aws_security_group.serverguard.id]
  associate_public_ip_address = true

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  root_block_device {
    volume_size = 12
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name = "serverguard"
  }
}

# Terraform output feeds the Ansible inventory
resource "local_file" "ansible_inventory" {
  filename        = "${path.module}/../ansible/inventory/aws.ini"
  file_permission = "0644"
  content = templatefile("${path.module}/templates/inventory.tftpl", {
    public_ip        = aws_instance.serverguard.public_ip
    private_key_path = pathexpand(var.private_key_path)
  })
}
