variable "aws_region" {
  description = "AWS region for all resources. ap-south-1 (Mumbai) has confirmed App Runner + RDS support."
  type        = string
  default     = "ap-south-1"
}

variable "project_name" {
  description = "Short identifier used to name all AWS resources."
  type        = string
  default     = "littleloot"
}

variable "environment" {
  description = "Deployment environment tag."
  type        = string
  default     = "production"
}

# ── Networking ───────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "private_subnet_cidrs" {
  description = "Two private subnet CIDRs for RDS multi-AZ (must be in different AZs)."
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

# ── RDS ─────────────────────────────────────────────────────────────────────

variable "db_instance_class" {
  description = "RDS instance type. db.t3.micro is free-tier eligible; use db.t3.small for production."
  type        = string
  default     = "db.t3.micro"
}

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "littleloot"
}

variable "db_multi_az" {
  description = "Enable Multi-AZ for RDS (recommended in production; adds cost)."
  type        = bool
  default     = true
}

variable "db_allocated_storage" {
  description = "Storage in GB. RDS autoscales above this floor."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  description = "Maximum storage autoscaling ceiling in GB."
  type        = number
  default     = 100
}

# ── App Runner ───────────────────────────────────────────────────────────────

variable "app_runner_cpu" {
  description = "vCPU units for App Runner instances (1024 = 1 vCPU)."
  type        = string
  default     = "1024"
}

variable "app_runner_memory" {
  description = "Memory for App Runner instances in MB."
  type        = string
  default     = "2048"
}

variable "app_runner_min_instances" {
  description = "Minimum running instances (0 = auto-pause when idle, saves cost)."
  type        = number
  default     = 1
}

variable "app_runner_max_instances" {
  description = "Maximum concurrent instances."
  type        = number
  default     = 5
}

# ── ECR ──────────────────────────────────────────────────────────────────────

variable "ecr_image_tag_mutability" {
  description = "MUTABLE allows tag overwrite (simpler for CI). IMMUTABLE is safer."
  type        = string
  default     = "MUTABLE"
}

variable "ecr_keep_image_count" {
  description = "Number of most recent images to keep; older ones are auto-deleted."
  type        = number
  default     = 10
}
