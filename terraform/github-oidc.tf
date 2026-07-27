# CI/CD — GitHub OIDC federation for the Deploy workflow (.github/workflows/deploy.yml).
#
# ⚠️ DELIBERATE EXCEPTION to this configuration's import-only doctrine: unlike every other file
# here (which adopts EXISTING resources under a plan-to-zero guarantee), this file CREATES two new
# resources when applied. Apply it once, deliberately, via the MFA-gated operator flow
# (`source ./assume.sh && terraform apply -target=aws_iam_openid_connect_provider.github \
# -target=aws_iam_role.github_deploy`) — see docs/CICD_PIPELINE.md.
#
# What the role can do — and ALL it can do:
#   * ssm:SendCommand with the AWS-RunShellScript document, ONLY to instances tagged Name=ih-beta;
#   * read back its own invocations (Get/List — required to stream output into the Actions log).
# It cannot touch S3, IAM, EC2 control-plane, Terraform state, or any other instance. No SSH keys,
# no static credentials: GitHub presents a short-lived OIDC token, AWS verifies repo + branch/env.

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # Pinned per AWS guidance; AWS now trusts GitHub's root CA regardless, but the field is required.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

resource "aws_iam_role" "github_deploy" {
  name                 = "hidden-view-github-deploy"
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        # The Deploy job runs in the `production` environment; dispatched and branch runs both
        # present that sub claim. Tighten to specific branches by replacing the wildcard.
        StringLike = {
          "token.actions.githubusercontent.com:sub" = [
            "repo:greenwichg/random_walks_with_erasure:environment:production",
            "repo:greenwichg/random_walks_with_erasure:ref:refs/heads/main",
            "repo:greenwichg/random_walks_with_erasure:ref:refs/heads/master",
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_deploy_ssm" {
  name = "deploy-via-ssm"
  role = aws_iam_role.github_deploy.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RunTheDeployScript"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:aws:ssm:us-east-1::document/AWS-RunShellScript"
      },
      {
        Sid      = "OnlyOnTheProductionHost"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:aws:ec2:us-east-1:652615011843:instance/*"
        Condition = {
          StringEquals = { "ssm:resourceTag/Name" = "ih-beta" }
        }
      },
      {
        Sid      = "ReadBackTheInvocation"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations", "ssm:DescribeInstanceInformation"]
        Resource = "*"
      },
      {
        # Suspend / Resume (.github/workflows/{suspend,resume}.yml). START and STOP only — the role
        # deliberately has NO ec2:TerminateInstances, NO ec2:DeleteVolume, NO ec2:DetachVolume: the
        # workflows cannot destroy the root volume that holds the database, even if a future edit
        # asked them to. Tag-scoped to the production instance.
        Sid      = "SuspendResumeTheProductionHost"
        Effect   = "Allow"
        Action   = ["ec2:StartInstances", "ec2:StopInstances"]
        Resource = "arn:aws:ec2:us-east-1:652615011843:instance/*"
        Condition = {
          StringEquals = { "ec2:ResourceTag/Name" = "ih-beta" }
        }
      },
      {
        # Describe* has no resource-level permissions in EC2; read-only, so "*" is the only option.
        Sid      = "ReadInstanceState"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus"]
        Resource = "*"
      },
    ]
  })
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository variable for .github/workflows/deploy.yml."
  value       = aws_iam_role.github_deploy.arn
}
