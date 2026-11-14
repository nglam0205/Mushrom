# Mushrom Build Tool

A Linux-based ROM/firmware build tool for patching APK/JAR files, rebuilding images, packaging releases, and running automated builds with GitHub Actions.

Mushrom builds use `colourmushrom_` or `hypermushrom_` package names and a
`MUSHROMddMMyy` build stamp. Generated images include `ro.mushrom.name`,
`ro.mushrom.version`, and `ro.mushrom.build`. Existing Android package IDs,
framework class paths, and `ro.hypervs.*` properties support the bundled apps.
The `build.yml` workflow checks out the repository containing the workflow.

The companion website source is in `leak/server/app/` relative to the workspace
root. Its `/mushrom` and `/colourmushrom` pages use the Mushrom template, with
the existing page routes also available. Its ROM lookups accept both naming
formats. Existing APK and Windows EXE binaries require their own source builds
to change embedded names and artwork.

> This project is intended for users who already understand Android ROMs, fastboot, payload/super images, and system patching. Always back up your data before flashing.

## Clone The Project

```bash
git clone https://github.com/<user>/<repo>.git
cd <repo>
chmod +x ./bhlnk bin/*
```

Replace `<user>/<repo>` with the real GitHub repository path for your project.

## Local Setup

Ubuntu/Debian or a similar Linux runner is recommended.

```bash
sudo apt-get update
sudo apt-get install -y \
  git lz4 wget zip unzip android-sdk-libsparse-utils brotli aria2 \
  python3-pip zipalign apksigner xmlstarlet aapt p7zip-full

python3 -m pip install --user ConfigObj pyTelegramBotAPI pytz requests cryptography
```

Download `rclone1` if you need upload or sync support:

```bash
aria2c -x 16 -s 16 -j 1 -o bin/rclone1 \
  https://github.com/buihien224/toolbox_notification/releases/download/rclone/rclone1
chmod +x bin/rclone1
```

### rclone uploads

Build uploads, test uploads, module sync, and cloud downloads use `bin/rclone1`.
By default the tool reads `bin/rclone.conf`; set `RCLONE_CONFIG` to use another
configuration file:

```bash
export RCLONE_CONFIG="$HOME/.config/rclone/rclone.conf"
export RCLONE_CONFIG_PASS='<config-password>' # only for encrypted configs
```

The configuration must contain a remote named `OneDrive`. Upload commands verify
the remote file size after `rclone copyto` completes:

```bash
./bhlnk upload
./bhlnk ut
```

## Quick Usage

Show help:

```bash
./bhlnk help
```

Build a patched ROM from a device codename or OTA URL:

```bash
./bhlnk build <codename-or-url> <builder-name>
```

Examples:

```bash
./bhlnk build cupid LocalBuild
./bhlnk build "https://example.com/rom.zip" LocalBuild
```

Create a stock package:

```bash
./bhlnk stock <codename-or-url> <builder-name>
```

Build the newest stock firmware from a specific ColorOS region:

```bash
./bhlnk stock CPH2797 vn
# Equivalent explicit option:
./bhlnk stock CPH2797 --region VN
```

You can also pass a direct ROM link to download first, then build the
stock package:

```bash
./bhlnk stock "https://example.com/rom.zip" MyBuilder
```

The region is optional. Device codes beginning with `CPH` default to `in`;
other unmapped ColorOS codes default to `cn`:

```bash
./bhlnk stock CPH2997
```

The same CPH-to-IN default applies in the regular build flow:

```bash
./bhlnk build CPH2997
```

An optional builder name can follow the region:

```bash
./bhlnk stock CPH2797 vn MyBuilder
```

Add `--backup` to save the extracted stock images before packaging:

```bash
./bhlnk stock CPH2797 vn --backup
```

Download the latest ROM by codename:

```bash
./bhlnk download <codename>
```

Download and rename the ROM archive from its OTA metadata without detecting or
extracting the firmware:

```bash
./bhlnk download-only CPH2841
```

Enable root ADB without host-key authentication in a recovery image while
preserving the stock input image:

```bash
./bhlnk recovery-adb recovery.img recovery-adb.img
./bhlnk recovery-adb recovery.img --dry-run
```

For ColorOS devices, pass a region directly after the codename:

```bash
./bhlnk download CPH2791 vn
```

The command-line region takes precedence over `COLOROS_REGION` and
`fastboot/coloros_region.list`.

For ColorOS device codes, the tool uses
[OPlus-Tracker](https://github.com/JerryTse-OSS/OPlus-Tracker) to query the
official OPlus OTA servers across the `A/C/F/H/J` channels, selects the newest
full build, resolves its `downloadCheck` link, and downloads the resulting
signed URL. The required OPlus-Tracker protocol, server configuration, and
resolver are integrated directly into `bin/coloros_resolver.py`; nothing is
cloned at build time.

You can override the OTA region:

```bash
export COLOROS_REGION="IN"
./bhlnk download CPH2649
```

Default regions for supported Global models are configured in
`fastboot/coloros_region.list`. `COLOROS_REGION` takes precedence over that
file. Supported region keys are:
`cn`, `cn_cmcc`, `in`, `eu`, `gl`, `vn`, `id`, `sg`, `th`, `ru`, `tr`,
`tw`, `my`, `jp`, `hk`, `au`, `ph`, `lk`, `eea`, `roe`, `kz`, `mx`, `eg`,
`8d`, `sa`, `latam`, `la`, `br`, `ex`, `apc`, `oca`, and `mea`.

To query the newest firmware from every supported OPlus region and
interactively choose a specific regional version to build:

```bash
./bhlnk check PLG110
```

The `check` command asks for confirmation before starting the build. Regular
`download`, `build`, and `stock` commands continue to select the newest
firmware automatically. Downloads show live percentage and speed when using
`aria2c`, `curl`, or `wget`.

Run the build steps manually:

```bash
./bhlnk i <builder-name>
./bhlnk extract
./bhlnk patchvs <builder-name>
./bhlnk pack
```

Common commands:

```bash
./bhlnk clean              # remove temporary build/download files
./bhlnk sync               # sync module/update from cloud storage
./bhlnk upload             # upload the final package through rclone
./bhlnk ut                 # upload through rclone to TESTBUILD
./bhlnk buildtest <code>   # build a test supertest.img
./bhlnk stocktest <code>   # build a stock test supertest.img
./bhlnk supertest [--a|--b] # build/flash supertest.img (mặc định: slot đang active trên máy qua adb/fastboot)
```

## GitHub Actions

The workflow file is:

```text
.github/workflows/build.yml
```

The workflow is manually triggered with `workflow_dispatch` and supports these inputs:

| Input | Description |
| --- | --- |
| `ROM_URL` | OTA URL or device codename |
| `mode` | `vs` for a patched ROM (one OTA zip for Mushrom and MushromTool), `stock` for a plain stock package (unlocked, same format) |
| `key` | Active key used to authorize the workflow run |
| `trigger` | Builder name shown in logs and notifications |

### Required Secrets

Open your GitHub repository, then go to `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`.

Required secrets:

| Secret | Purpose |
| --- | --- |
| `AUTH_KEY` | Compared with the `key` workflow input; the build only runs when it matches |
| `CLONE_KEY` | SSH private key used by the checkout step for private repositories |

If your repository is public, or you do not need a custom SSH key for checkout, adjust the `Checkout` step in `.github/workflows/build.yml`.

The workflow currently checks out:

```yaml
repository: buihien224/vietsub_erofs_tool
```

If you fork or rename the repository, change this value to your repository, or remove `repository:` to checkout the repository that is running the workflow.

### Run The Workflow

1. Open the `Actions` tab on GitHub.
2. Select the `Mushrom Build Tool` workflow.
3. Click `Run workflow`.
4. Enter `ROM_URL`, select `mode`, enter `key`, and set `trigger`.
5. Click `Run workflow` and follow the logs.

Example input:

```text
ROM_URL=cupid
mode=build
key=<your-AUTH_KEY>
trigger=Auto_Build
```

## Output

After a successful build, the tool packages the final file in the repository directory and uploads it when rclone/notification settings are available. The final filename is generated from device information, base ROM, patch version, and build type.

Common temporary directories:

```text
zip_temp/
img_temp/
jar_temp/
backup/
<device>/firmware-update/
```

## Notes

- Local builds require enough free disk space because ROM files and super images can be very large.
- Some steps require `sudo`.
- Do not run the tool inside a directory containing important untracked data. The `clean` command removes temporary build/download files from the project root.
- APK/JAR patching logic mainly lives in `bin/apk.sh`; patch assets and configuration files live under `module/`.
