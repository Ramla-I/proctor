To build and run the container:

```bash
./download.sh
docker build -t proctor:april2026 .
docker run -it proctor:april2026 .
```

Inside the container, to translate all test cases and execute test vectors:

```bash
./translate_all.py
./Test-Corpus/deployment/scripts/github-actions/run_rust.sh --keep-going -m P00 -m P01
```

---

To translate an individual test case:

```bash
./translate.py \
  bundles/Public-Tests/P01_sphinx/005_sphincs_PQCgenKAT_sign_blake_128f_simple.tar.gz \
  Test-Corpus/Public-Tests/P01_sphinx/005_sphincs_PQCgenKAT_sign_blake_128f_simple/translated_rust
```

To test an individual test case:

```bash
./Test-Corpus/deployment/scripts/github-actions/run_rust.sh \
  -m Public-Tests/P01_sphinx/005_sphincs_PQCgenKAT_sign_blake_128f_simple
```
