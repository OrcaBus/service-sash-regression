FROM public.ecr.aws/lambda/python:3.12

# bcftools needed by comprehensive_sash_comparison.py
RUN dnf install -y bcftools && dnf clean all

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY comparator/ ${LAMBDA_TASK_ROOT}/comparator/

CMD ["comparator.handler.handler"]
