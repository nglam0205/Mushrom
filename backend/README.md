# VS Backend

Single Gradle project for the Java sources injected into `framework.jar`.

- `vsteam`: unified VS framework and attestation backend; produces `vsteam.apk`.
- `subattest`: retained legacy module directory; the implementation now lives
  directly in `vsteam`.

Build the single release APK with:

```sh
make build
```

`bin/apk.sh` runs the same build automatically from `preparefiles`, then
decodes and stages the merged `vsteam.apk` backend.
