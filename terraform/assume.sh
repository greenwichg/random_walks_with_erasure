# Load short-lived HiddenViewTerraformExec credentials into your shell so Terraform
# can reach AWS. Needed because the Terraform S3 backend cannot prompt for MFA:
# we assume the exec role with the AWS CLI (which can prompt), then export the
# resulting temporary session credentials as AWS_* environment variables for Terraform.
#
# Usage (must be SOURCED, not executed):
#     source ./assume.sh        # or:  . ./assume.sh
#
# Re-source when the ~1h session expires (Terraform will start returning
# ExpiredToken errors). Do not commit any credentials — these live only in your shell.

# Guard: warn if run as an executable (a subshell's exports would vanish immediately).
if [ -n "${BASH_SOURCE:-}" ] && [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  echo "Run with 'source ./assume.sh' (or '. ./assume.sh'), not as ./assume.sh" >&2
  exit 1
fi

_hv_profile="hv-terraform"
# 1) Trigger the interactive MFA prompt via the assume-role profile (caches the session).
# 2) Export the resolved temporary credentials into the current shell.
if aws sts get-caller-identity --profile "$_hv_profile" >/dev/null; then
  eval "$(aws configure export-credentials --profile "$_hv_profile" --format env)"
  echo "Loaded credentials for: $(aws sts get-caller-identity --query Arn --output text)"
else
  echo "Could not assume '$_hv_profile' — check your MFA code and ~/.aws config." >&2
fi
unset _hv_profile
