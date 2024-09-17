#FROM clearlinux/tesseract-ocr
#FROM franky1/tesseract
FROM ghcr.io/clearlinux/tesseract-ocr:42360
COPY ./ocr /app
ENV TESSDATA_PREFIX /app
RUN swupd bundle-add python3-basic
RUN mkdir /data
# RUN pip install --upgrade pip

# WORKDIR /home/mruser
WORKDIR /code
COPY ./requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /code

EXPOSE 80

CMD ["uvicorn", "main:app","--host", "tessarest", "--port", "80", "--reload" ]