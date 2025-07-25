FROM python:3.12-slim

WORKDIR /app

LABEL name="OOTD Bot" authors="Darlene Jiang" repository="https://github.com/TerenceEzreal/XHS-Downloader"

COPY requirements.txt /app/requirements.txt
RUN pip config --user set global.progress_bar off
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY locale /app/locale
COPY source /app/source
COPY static/XHS-Downloader.tcss /app/static/XHS-Downloader.tcss
COPY LICENSE /app/LICENSE
COPY main.py /app/main.py
COPY bot.py /app/bot.py
COPY config.ini /app/config.ini


EXPOSE 5556

CMD ["python", "bot.py"]
