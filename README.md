To build and run the container:

```bash
git submodule init
git submodule update
./download.sh
docker build -t proctor:june2026 .
docker run -it proctor:june2026
```

Inside the container, to translate all test cases and execute test vectors:

```bash
cd /home/ubuntu && ./scripts/orchestrate_all.py bundles Test-Corpus
cd /home/ubuntu/Test-Corpus && ./deployment/scripts/github-actions/run_rust.sh --keep-going

cd /home/ubuntu && ./scripts/orchestrate_all.py PUBLIC-bundles PUBLIC-Test-Corpus
cd /home/ubuntu/PUBLIC-Test-Corpus && ./deployment/scripts/github-actions/run_rust.sh --keep-going
```

---

To translate a single test case:

```bash
cd /home/ubuntu && \
./scripts/orchestrate.py \
  bundles/Public-Tests/B01_synthetic/001_helloworld.tar.gz \
  Test-Corpus/Public-Tests/B01_synthetic/001_helloworld/translated_rust
```

To test a single test case:

```bash
cd /home/ubuntu/Test-Corpus && \
./deployment/scripts/github-actions/run_rust.sh \
  -m '^Public-Tests/B01_synthetic/001_helloworld$'
```
