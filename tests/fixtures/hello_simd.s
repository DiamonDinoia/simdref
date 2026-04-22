	.file	"hello_simd.c"
	.text
	.globl	dot8
	.type	dot8, @function
dot8:
.LFB0:
	.cfi_startproc
	vmovups	(%rdi), %ymm0
	vmovups	(%rsi), %ymm1
	vmulps	%ymm1, %ymm0, %ymm0
	vaddps	%ymm0, %ymm0, %ymm0
	vxorps	%xmm1, %xmm1, %xmm1
	vhaddps	%ymm0, %ymm0, %ymm0
	ret
	.cfi_endproc
.LFE0:
	.size	dot8, .-dot8
