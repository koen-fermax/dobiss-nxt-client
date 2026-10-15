# Publishing

The package is prepared for PyPI Trusted Publishing using GitHub Actions.
No long-lived PyPI token is required.

For the first release, sign in to PyPI as the account that will own the project
and add a **pending publisher** at https://pypi.org/manage/account/publishing/:

| Field | Value |
| --- | --- |
| PyPI project name | `dobiss-nxt-client` |
| GitHub owner | `koen-fermax` |
| GitHub repository | `dobiss-nxt-client` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

The workflow filename is only the basename, not `.github/workflows/publish.yml`.
The environment name must match exactly. The pending publisher creates the project
on the first successful upload; it does not reserve the package name.

The GitHub workflow **Publish to PyPI** supports a validation-only run with
`publish=false` (the default). After the PyPI publisher is configured, dispatch the
workflow from `main` with `publish=true`. It tests the client, builds a wheel and
source distribution, checks package metadata and publishes those same artifacts
in a separate OIDC-enabled job.

Do not rerun publication of an already published version. PyPI does not permit
replacing release files. For subsequent releases, update the package version,
validate the change and use the same workflow.

References:

- https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/
- https://docs.pypi.org/trusted-publishers/using-a-publisher/
