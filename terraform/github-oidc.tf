# GitHub Actions OIDC — keyless AWS authentication.
# This lets GitHub Actions assume an IAM role without storing long-lived
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in GitHub Secrets.
#
# Usage in workflow:
#   - uses: aws-actions/configure-aws-credentials@v4
#     with:
#       role-to-assume: <aws_iam_role.github_actions_deploy.arn>
#       aws-region: ap-south-1

variable "github_org" {
  description = "GitHub organisation or username that owns the repository."
  type        = string
  default     = "IntelligenceHubCreates"
}

variable "github_repo" {
  description = "Repository name (without the org prefix)."
  type        = string
  default     = "littleloot-backend"
}

# OIDC provider for GitHub Actions (one per AWS account; safe to have duplicates
# — Terraform will import the existing one if you run apply twice).
data "aws_iam_openid_connect_provider" "github" {
  count = 0  # set to 1 if the provider does not yet exist in your account
  url   = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "github_actions_deploy" {
  name = "${var.project_name}-github-actions-deploy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          # Only the main branch of the backend repo can assume this role.
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  name = "${var.project_name}-github-actions-deploy-policy"
  role = aws_iam_role.github_actions_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ECR: login, push images
      {
        Effect   = "Allow"
        Action   = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:GetRepositoryPolicy",
          "ecr:DescribeRepositories",
          "ecr:ListImages",
          "ecr:DescribeImages",
          "ecr:BatchGetImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
        ]
        Resource = "*"
      },
      # App Runner: trigger and observe deployments
      {
        Effect   = "Allow"
        Action   = [
          "apprunner:StartDeployment",
          "apprunner:DescribeService",
          "apprunner:ListOperations",
        ]
        Resource = aws_apprunner_service.backend.arn
      },
    ]
  })
}

output "github_actions_role_arn" {
  description = "Add this as the AWS_DEPLOY_ROLE_ARN GitHub secret."
  value       = aws_iam_role.github_actions_deploy.arn
}
