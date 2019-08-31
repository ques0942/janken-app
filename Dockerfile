FROM python:3

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV FLASK_APP=app
CMD [ "python", "-m", "flask", "run", "--host=0.0.0.0", "--port=8080" ]
