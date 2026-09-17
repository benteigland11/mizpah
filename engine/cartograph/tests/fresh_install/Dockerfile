FROM python:3.12-slim

# No extras - bare Python, just like a real user
RUN pip install --no-cache-dir cartograph-cli

COPY test_fresh_install.sh /test_fresh_install.sh
RUN chmod +x /test_fresh_install.sh

WORKDIR /workspace
CMD ["/test_fresh_install.sh"]
