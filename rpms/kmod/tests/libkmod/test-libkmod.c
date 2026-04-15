/* SPDX-License-Identifier: LGPL-2.1+
# ~~~
#   Description: Tests libkmod
#
#   Author: Susant Sahani <susant@redhat.com>
#   Copyright (c) 2018 Red Hat, Inc.
# ~~~
*/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libkmod.h>
#include <setjmp.h>
#include <inttypes.h>
#include <cmocka.h>

static int load_module(void **state) {
        struct kmod_list *list = NULL;
        struct kmod_ctx *ctx = NULL;
        struct kmod_list *l;
        int r;

        assert_non_null((ctx = kmod_new(NULL, NULL)));
        assert_return_code(kmod_module_new_from_lookup(ctx, "ipip", &list), 0);

        kmod_list_foreach(l, list) {
                struct kmod_module *mod = NULL;

                mod = kmod_module_get_module(l);
                assert_non_null(mod);

                assert_return_code(kmod_module_probe_insert_module(mod, 0, NULL, NULL, NULL, NULL), 0);
        }

        free(ctx);

        return 0;
}

static int remove_module(void **state) {
        struct kmod_list *list = NULL;
        struct kmod_ctx *ctx = NULL;
        struct kmod_list *l;
        int r;

        assert_non_null((ctx = kmod_new(NULL, NULL)));
        assert_return_code(kmod_module_new_from_lookup(ctx, "ipip", &list), 0);

        kmod_list_foreach(l, list) {
                struct kmod_module *mod = NULL;

                mod = kmod_module_get_module(l);
                assert_non_null(mod);

                assert_return_code(kmod_module_remove_module(mod, 0), 0);
        }

        free(ctx);

        return 0;
}

void test_load_module(void **state) {
        load_module(NULL);
}

void test_remove_module(void **state) {
        remove_module(NULL);
}

int main(int argc, char *argv[]) {
        const struct CMUnitTest libmod_tests[] = {
                                                  cmocka_unit_test_teardown(test_load_module, remove_module),
                                                  cmocka_unit_test_setup(test_remove_module, load_module),
        };

        return cmocka_run_group_tests(libmod_tests, NULL, NULL);
}
