package main

import (
	"crypto/md5"
	"fmt"
	"os"
)

// This program attempts to use MD5, which is not a FIPS-approved algorithm.
// When run with GODEBUG=fips140=only, the md5.New() call should panic
// because MD5 is not permitted in strict FIPS mode.
func main() {
	fmt.Println("Attempting MD5 (non-FIPS-approved algorithm)...")
	h := md5.New()
	h.Write([]byte("test"))
	digest := fmt.Sprintf("%x", h.Sum(nil))
	fmt.Printf("MD5 digest: %s\n", digest)
	fmt.Println("MD5 succeeded — this should NOT happen in fips140=only mode")
	os.Exit(0)
}
