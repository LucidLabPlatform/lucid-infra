FROM alpine:3.19
RUN apk add --no-cache chrony
COPY chrony-server.conf /etc/chrony/chrony.conf
EXPOSE 123/udp
CMD ["chronyd", "-d", "-f", "/etc/chrony/chrony.conf"]
