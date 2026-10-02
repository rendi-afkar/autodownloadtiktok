.PHONY: all install

all: install main

install:
	pkg install -y python python-cryptography clang

main: main.c
	cc -O2 -o main main.c
	@echo "Selesai. Jalankan dengan: ./main"
