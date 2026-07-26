chal: chal.c Makefile
	gcc chal.c -o chal -pie -fstack-protector-all -Wl,-z,now -Wl,-rpath,. -Wl,--dynamic-linker,./ld-linux-x86-64.so.2 -Wno-unused-result -g3 -ffile-prefix-map=$(PWD)=.