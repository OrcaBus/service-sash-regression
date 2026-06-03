FROM public.ecr.aws/lambda/python:3.12

# bcftools needed by comprehensive_sash_comparison.py (not in AL2023 dnf, compile from source)
RUN dnf install -y gcc make autoconf zlib-devel bzip2 bzip2-devel xz-devel libcurl-devel ncurses-devel tar && \
    curl -fL -o /tmp/bcftools.tar.bz2 https://github.com/samtools/bcftools/releases/download/1.21/bcftools-1.21.tar.bz2 && \
    tar -C /tmp -xjf /tmp/bcftools.tar.bz2 && \
    cd /tmp/bcftools-1.21 && make && make install PREFIX=/usr/local && \
    rm -rf /tmp/bcftools* && \
    dnf clean all

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY comparator/ ${LAMBDA_TASK_ROOT}/comparator/
COPY submitter/ ${LAMBDA_TASK_ROOT}/submitter/

CMD ["comparator.lambdas.comparator.handler.handler"]
