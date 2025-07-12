FROM python:3.11-alpine AS builder

WORKDIR /opt/pysetup

RUN apk update && \
    apk add --no-cache gcc musl-dev mariadb-connector-c-dev linux-headers

COPY requirements.txt .

RUN pip install --no-cache-dir --target /opt/pysetup/deps -r requirements.txt

FROM python:3.11-alpine

WORKDIR /workspace	

RUN apk update && \
    apk add --no-cache mariadb-connector-c

COPY --from=builder /opt/pysetup/deps /usr/local/lib/python3.11/site-packages

COPY . .

RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser

CMD ["python", "main.py"]