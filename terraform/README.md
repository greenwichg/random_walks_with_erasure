# Hidden View — Terraform (production infrastructure)

Import-only migration of the **existing** production AWS infrastructure into
Terraform. This configuration **creates and recreates nothing**: every resource is
brought under management via `import`, and each import is gated on a **plan-to-zero**
result (`0 to add, 0 to change, 0 to destroy`).

## Prerequisites

- **Terraform >= 1.10** (S3-native state locking).
- **AWS CLI v2** with two profiles (see the migration runbook):
  - `hidden-view-deployer` — admin source profile (rotated static key).
  - `hv-terraform` — assumes `HiddenViewTerraformExec` (least-privilege,
    read-only-first, boundary-capped), **MFA-gated**. All Terraform runs use this.

## State backend

Remote state lives in `s3://hidden-view-terraform-state-652615011843/prod/terraform.tfstate`
(us-east-1): versioning on, SSE-S3 encryption, public access blocked, TLS-only, and
S3-native locking (`use_lockfile = true`). The bucket is **bootstrap infrastructure**,
created out-of-band by the admin — the exec role deliberately cannot create buckets.

## Usage

```
cd terraform
terraform init      # initialise the S3 backend (prompts for your MFA code)
terraform plan      # after imports, MUST be 0 add / 0 change / 0 destroy
```

Terraform assumes the exec role via the `hv-terraform` profile, so you are prompted
for an MFA code on operations that touch AWS.
