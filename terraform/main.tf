terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }

  # Uncomment and fill in after creating the S3 bucket + DynamoDB table for state.
  # backend "s3" {
  #   bucket         = "littleloot-tf-state"
  #   key            = "production/terraform.tfstate"
  #   region         = "ap-south-1"
  #   dynamodb_table = "littleloot-tf-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

# ── Data: available AZs ───────────────────────────────────────────────────────
data "aws_availability_zones" "available" {
  state = "available"
  # Exclude local zones (e.g. Delhi aps1-del1-az1) — App Runner and RDS require
  # standard AZs only. opt-in-not-required = the 3 standard ap-south-1 AZs.
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

# ── ECR Repository ────────────────────────────────────────────────────────────
resource "aws_ecr_repository" "backend" {
  name                 = "${var.project_name}-backend"
  image_tag_mutability = var.ecr_image_tag_mutability

  image_scanning_configuration {
    scan_on_push = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last ${var.ecr_keep_image_count} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.ecr_keep_image_count
      }
      action = { type = "expire" }
    }]
  })
}

# ── VPC ───────────────────────────────────────────────────────────────────────
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${var.project_name}-vpc" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${var.project_name}-private-${count.index + 1}" }
}

# Internet Gateway (needed for App Runner's public-facing endpoint, not the VPC connector)
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

# ── Security Groups ───────────────────────────────────────────────────────────

# RDS: allow inbound PostgreSQL only from App Runner via VPC connector
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg"
  description = "Allow PostgreSQL from App Runner VPC connector"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from App Runner"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app_runner_connector.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-rds-sg" }
}

# App Runner VPC Connector security group
resource "aws_security_group" "app_runner_connector" {
  name        = "${var.project_name}-apprunner-sg"
  description = "Outbound access for App Runner VPC connector"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-apprunner-sg" }
}

# ── RDS ───────────────────────────────────────────────────────────────────────

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnet-group"
  subnet_ids = aws_subnet.private[*].id
  tags       = { Name = "${var.project_name}-db-subnet-group" }
}

# Credentials are pulled from Secrets Manager at runtime by the app.
# The master password for RDS itself is generated here and stored in Secrets Manager.
resource "random_password" "db_password" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_db_instance" "main" {
  identifier = "${var.project_name}-db"

  engine               = "postgres"
  engine_version       = "15.18"
  instance_class       = var.db_instance_class
  allocated_storage    = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type         = "gp3"
  storage_encrypted    = true

  db_name  = var.db_name
  username = "${var.project_name}admin"
  password = random_password.db_password.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  multi_az                    = var.db_multi_az
  publicly_accessible         = false
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${var.project_name}-final-snapshot"
  backup_retention_period     = 7
  backup_window               = "03:00-04:00"   # UTC 08:30–09:30 IST
  maintenance_window          = "sun:04:00-sun:05:00"
  auto_minor_version_upgrade  = true
  performance_insights_enabled = true

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [password]   # Rotated via Secrets Manager, not Terraform
  }

  tags = { Name = "${var.project_name}-db" }
}

# ── Secrets Manager ───────────────────────────────────────────────────────────
# All application secrets in ONE secret object; App Runner injects them as env vars.

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.project_name}/production"
  description             = "All application secrets for ${var.project_name} production"
  recovery_window_in_days = 7

  tags = { Name = "${var.project_name}-secrets" }
}

resource "aws_secretsmanager_secret_version" "app_initial" {
  secret_id = aws_secretsmanager_secret.app.id

  # Initial skeleton — operator MUST update these values after first apply.
  # Terraform stores this in state; use `terraform output` or the AWS console to check.
  secret_string = jsonencode({
    POSTGRES_USER                = "${var.project_name}admin"
    POSTGRES_PASSWORD            = random_password.db_password.result
    POSTGRES_SERVER              = aws_db_instance.main.address
    POSTGRES_PORT                = "5432"
    POSTGRES_DB                  = var.db_name
    DATABASE_URL                 = "postgresql://${var.project_name}admin:${random_password.db_password.result}@${aws_db_instance.main.address}:5432/${var.db_name}"
    SECRET_KEY                   = "REPLACE_AFTER_APPLY_openssl_rand_base64_32"
    ALGORITHM                    = "HS256"
    CLOUDINARY_CLOUD_NAME        = "REPLACE_WITH_CLOUDINARY_CLOUD_NAME"
    CLOUDINARY_API_KEY           = "REPLACE_WITH_CLOUDINARY_API_KEY"
    CLOUDINARY_API_SECRET        = "REPLACE_WITH_CLOUDINARY_API_SECRET"
    RAZORPAY_KEY_ID              = "REPLACE_WITH_RAZORPAY_KEY_ID"
    RAZORPAY_KEY_SECRET          = "REPLACE_WITH_RAZORPAY_KEY_SECRET"
    RAZORPAY_WEBHOOK_SECRET      = "REPLACE_WITH_RAZORPAY_WEBHOOK_SECRET"
    RESEND_API_KEY               = "REPLACE_WITH_RESEND_API_KEY"
    RESEND_FROM_EMAIL            = "noreply@littlelootgifts.com"
    INITIAL_ADMIN_EMAIL          = "REPLACE_WITH_ADMIN_EMAIL"
    INITIAL_ADMIN_PASSWORD_HASH  = "REPLACE_WITH_BCRYPT_HASH"
    ENVIRONMENT                  = "production"
    ALLOWED_ORIGINS              = "https://littlelootgifts.com,https://www.littlelootgifts.com"
    FRONTEND_URL                 = "https://littlelootgifts.com"
  })

  lifecycle {
    ignore_changes = [secret_string]   # Updated manually / via rotation, not Terraform
  }
}

# ── IAM role for App Runner ───────────────────────────────────────────────────

resource "aws_iam_role" "app_runner_instance" {
  name = "${var.project_name}-apprunner-instance-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "app_runner_secrets" {
  name = "${var.project_name}-apprunner-secrets-policy"
  role = aws_iam_role.app_runner_instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      Resource = aws_secretsmanager_secret.app.arn
    }]
  })
}

resource "aws_iam_role" "app_runner_ecr_access" {
  name = "${var.project_name}-apprunner-ecr-access-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "build.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "app_runner_ecr" {
  role       = aws_iam_role.app_runner_ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# ── App Runner VPC Connector ──────────────────────────────────────────────────

resource "aws_apprunner_vpc_connector" "main" {
  vpc_connector_name = "${var.project_name}-vpc-connector"
  subnets            = aws_subnet.private[*].id
  security_groups    = [aws_security_group.app_runner_connector.id]

  tags = { Name = "${var.project_name}-vpc-connector" }
}

# ── App Runner Service ────────────────────────────────────────────────────────

resource "aws_apprunner_service" "backend" {
  service_name = "${var.project_name}-backend"

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.app_runner_ecr_access.arn
    }

    image_repository {
      image_identifier      = "${aws_ecr_repository.backend.repository_url}:latest"
      image_repository_type = "ECR"

      image_configuration {
        port = "8000"

        # Runtime environment variables — inject all secrets from Secrets Manager.
        # App Runner does not yet support native Secrets Manager injection as env vars,
        # so the entrypoint script must fetch them. We pass the secret ARN as an env var
        # and the app can use boto3 to fetch at startup if needed.
        # For now: pass critical vars explicitly; the rest come from the secret.
        runtime_environment_variables = {
          ENVIRONMENT         = "production"
          PORT                = "8000"
          GUNICORN_WORKERS    = "2"
          AWS_SECRETS_ARN     = aws_secretsmanager_secret.app.arn
          AWS_REGION          = var.aws_region
        }

        # Native Secrets Manager injection (each key becomes an env var)
        runtime_environment_secrets = {
          DATABASE_URL            = "${aws_secretsmanager_secret.app.arn}:DATABASE_URL::"
          SECRET_KEY              = "${aws_secretsmanager_secret.app.arn}:SECRET_KEY::"
          ALGORITHM               = "${aws_secretsmanager_secret.app.arn}:ALGORITHM::"
          CLOUDINARY_CLOUD_NAME   = "${aws_secretsmanager_secret.app.arn}:CLOUDINARY_CLOUD_NAME::"
          CLOUDINARY_API_KEY      = "${aws_secretsmanager_secret.app.arn}:CLOUDINARY_API_KEY::"
          CLOUDINARY_API_SECRET   = "${aws_secretsmanager_secret.app.arn}:CLOUDINARY_API_SECRET::"
          RAZORPAY_KEY_ID         = "${aws_secretsmanager_secret.app.arn}:RAZORPAY_KEY_ID::"
          RAZORPAY_KEY_SECRET     = "${aws_secretsmanager_secret.app.arn}:RAZORPAY_KEY_SECRET::"
          RAZORPAY_WEBHOOK_SECRET = "${aws_secretsmanager_secret.app.arn}:RAZORPAY_WEBHOOK_SECRET::"
          RESEND_API_KEY          = "${aws_secretsmanager_secret.app.arn}:RESEND_API_KEY::"
          RESEND_FROM_EMAIL       = "${aws_secretsmanager_secret.app.arn}:RESEND_FROM_EMAIL::"
          FRONTEND_URL            = "${aws_secretsmanager_secret.app.arn}:FRONTEND_URL::"
          ALLOWED_ORIGINS         = "${aws_secretsmanager_secret.app.arn}:ALLOWED_ORIGINS::"
          INITIAL_ADMIN_EMAIL     = "${aws_secretsmanager_secret.app.arn}:INITIAL_ADMIN_EMAIL::"
          INITIAL_ADMIN_PASSWORD_HASH = "${aws_secretsmanager_secret.app.arn}:INITIAL_ADMIN_PASSWORD_HASH::"
          POSTGRES_SERVER         = "${aws_secretsmanager_secret.app.arn}:POSTGRES_SERVER::"
          POSTGRES_PORT           = "${aws_secretsmanager_secret.app.arn}:POSTGRES_PORT::"
          POSTGRES_USER           = "${aws_secretsmanager_secret.app.arn}:POSTGRES_USER::"
          POSTGRES_PASSWORD       = "${aws_secretsmanager_secret.app.arn}:POSTGRES_PASSWORD::"
          POSTGRES_DB             = "${aws_secretsmanager_secret.app.arn}:POSTGRES_DB::"
        }
      }
    }

    auto_deployments_enabled = false   # CI/CD handles deploys; no auto on ECR push
  }

  instance_configuration {
    cpu               = var.app_runner_cpu
    memory            = var.app_runner_memory
    instance_role_arn = aws_iam_role.app_runner_instance.arn
  }

  network_configuration {
    egress_configuration {
      egress_type       = "VPC"
      vpc_connector_arn = aws_apprunner_vpc_connector.main.arn
    }
    ingress_configuration {
      is_publicly_accessible = true
    }
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/healthz"
    interval            = 20
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.main.arn

  tags = { Name = "${var.project_name}-backend" }

  depends_on = [aws_db_instance.main]
}

resource "aws_apprunner_auto_scaling_configuration_version" "main" {
  auto_scaling_configuration_name = "${var.project_name}-autoscaling"
  min_size                         = var.app_runner_min_instances
  max_size                         = var.app_runner_max_instances
  max_concurrency                  = 100

  tags = { Name = "${var.project_name}-autoscaling" }
}

# ── CloudWatch Log Groups ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "app_runner" {
  name              = "/aws/apprunner/${var.project_name}-backend"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "rds" {
  name              = "/aws/rds/instance/${var.project_name}-db/postgresql"
  retention_in_days = 30
}

# ── CloudWatch Alarms ─────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "rds_cpu_high" {
  alarm_name          = "${var.project_name}-rds-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "RDS CPU utilization above 80% for 10 minutes"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }
}
