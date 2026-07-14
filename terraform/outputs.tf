output "ecr_repository_url" {
  description = "ECR repository URL — used in CI/CD to push the Docker image."
  value       = aws_ecr_repository.backend.repository_url
}

output "app_runner_service_url" {
  description = "Public URL of the App Runner service (no custom domain yet)."
  value       = "https://${aws_apprunner_service.backend.service_url}"
}

output "app_runner_service_arn" {
  description = "ARN of the App Runner service — used by CI/CD to trigger deploys."
  value       = aws_apprunner_service.backend.arn
}

output "rds_endpoint" {
  description = "RDS hostname — already embedded in the DATABASE_URL secret."
  value       = aws_db_instance.main.address
  sensitive   = true
}

output "secrets_manager_arn" {
  description = "ARN of the Secrets Manager secret — update values there after first apply."
  value       = aws_secretsmanager_secret.app.arn
}

output "vpc_id" {
  description = "VPC ID."
  value       = aws_vpc.main.id
}

output "next_steps" {
  description = "Ordered action items after first terraform apply."
  value       = <<-EOT
    NEXT STEPS after terraform apply:
    1. Update all REPLACE_* values in Secrets Manager:
         aws secretsmanager update-secret \
           --secret-id ${aws_secretsmanager_secret.app.name} \
           --secret-string file://secrets.json
    2. Build and push the Docker image to ECR:
         aws ecr get-login-password --region ${var.aws_region} | \
           docker login --username AWS --password-stdin ${aws_ecr_repository.backend.repository_url}
         docker build -t ${aws_ecr_repository.backend.repository_url}:latest \
           Backend/Silvee-Backend/
         docker push ${aws_ecr_repository.backend.repository_url}:latest
    3. Deploy the App Runner service (the CI/CD workflow does this automatically):
         aws apprunner start-deployment \
           --service-arn ${aws_apprunner_service.backend.arn}
    4. Verify health check passes:
         curl https://${aws_apprunner_service.backend.service_url}/healthz
    5. Add custom domain in App Runner console → littlelootgifts.com/api
    6. Configure Vercel with BACKEND_URL=https://api.littlelootgifts.com
  EOT
}
