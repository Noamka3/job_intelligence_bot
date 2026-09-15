# certs/

Drop any extra root CA certificates here (`.crt`/`.pem`, gitignored - not
committed) that the Docker build needs to trust. The Dockerfile copies
this whole directory into the image's trust store and runs
`update-ca-certificates` - an empty directory is a harmless no-op, so
this isn't required on a machine that doesn't need it.

**Why this exists on this dev machine specifically**: Avast's antivirus
does TLS inspection with its own root certificate (see README.md's
"pip install fails with CERTIFICATE_VERIFY_FAILED" and "Any real HTTPS
call crashes the process..." troubleshooting entries for the same issue
on the Windows host and in Python directly). Docker Desktop's container
traffic goes through the same intercepted network, so `pip install`
inside the `worker`/`beat` image build fails the same way unless the
container also trusts that root cert. Fixed here by copying
`C:\Users\<you>\.certs\avast-root.pem` to `certs/avast-root.crt` before
building:

```bash
cp ~/.certs/avast-root.pem certs/avast-root.crt
docker compose build worker beat
```

On a machine without this kind of TLS-inspecting antivirus, this
directory can just stay empty.
