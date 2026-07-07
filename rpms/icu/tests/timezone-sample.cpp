#include <stdio.h>
#include <unicode/utypes.h>
#include <unicode/unistr.h>
#include <unicode/calendar.h>
#include <unicode/ustdio.h>
#include <unicode/datefmt.h>
#include <unicode/locid.h>

using namespace icu;

int fail_count = 0;
int pass_count = 0;

void test_dst(const char *timezone,
              int year,
              int month,
              int day,
              int hour,
              int minute,
              int second,
              int std_offset_hours_expected,
              int dst_offset_hours_expected) {
    UErrorCode success = U_ZERO_ERROR;
    UnicodeString dateReturned, curTZNameEn, curTZNameJp;
    UDate dateTime;
    int32_t stdOffset, dstOffset;
    int std_offset_hours, dst_offset_hours;

    TimeZone *tz = TimeZone::createTimeZone(timezone);

    curTZNameEn = tz->getDisplayName(Locale::getEnglish(), curTZNameEn);
    u_printf("%s-name-english=%S\n",
             timezone,
             curTZNameEn.getTerminatedBuffer());

    curTZNameJp = tz->getDisplayName(Locale::getJapanese(), curTZNameJp);
    u_printf("%s-name-japanese=%S\n",
             timezone,
             curTZNameJp.getTerminatedBuffer());

    Calendar *calendar = Calendar::createInstance(success);
    calendar->adoptTimeZone(TimeZone::createTimeZone("UTC"));

    DateFormat *dt = DateFormat::createDateTimeInstance(
        DateFormat::LONG, DateFormat::LONG, Locale("ja", "JP"));
    dt->adoptTimeZone(TimeZone::createTimeZone("UTC"));

    // Set time in UTC, note that month is from 0-11.
    calendar->set(year, month, day, hour, minute, second);
    dateTime = calendar->getTime(success);
    dateReturned = "";
    dateReturned = dt->format(dateTime, dateReturned, success);
    printf("current-time-utc-%4d-%d-%d-%d-%d-%d=",
           year, month, day, hour, minute, second);
    u_printf("%S\n", dateReturned.getTerminatedBuffer());
    tz->getOffset(dateTime, true, stdOffset, dstOffset, success);
    std_offset_hours = stdOffset/(1000*60*60);
    dst_offset_hours = dstOffset/(1000*60*60);

    printf("%s-std-offset-%4d-%d-%d-%d-%d-%d=%d\n",
           timezone,
           year, month, day, hour, minute, second,
           std_offset_hours);

    if (std_offset_hours != std_offset_hours_expected) {
        printf("std_offset_hours=%d, std_offset_hours_expected=%d\n",
               std_offset_hours, std_offset_hours_expected);
        printf("FAIL\n");
        fail_count++;
    }
    else {
        printf("PASS\n");
        pass_count++;
    }

    printf("%s-dst-offset-%4d-%d-%d-%d-%d-%d=%d\n",
           timezone,
           year, month, day, hour, minute, second,
           dst_offset_hours);

    if (dst_offset_hours != dst_offset_hours_expected) {
        printf("dst_offset_hours=%d, dst_offset_hours_expected=%d\n",
               dst_offset_hours, dst_offset_hours_expected);
        printf("FAIL\n");
        fail_count++;
    }
    else {
        printf("PASS\n");
        pass_count++;
    }

    delete calendar;
    delete dt;
    delete tz;
}

int main(int argc, char** argv) {

    // zdump -c 2019,2020 -v Asia/Gaza
    // Asia/Gaza  Fri Oct 25 20:59:59 2019 UT = Fri Oct 25 23:59:59 2019 EEST isdst=1 gmtoff=10800
    // Asia/Gaza  Fri Oct 25 21:00:00 2019 UT = Fri Oct 25 23:00:00 2019 EET isdst=0 gmtoff=7200

    // timezone, year, month (0-11), day, hour, minute, second,
    // std offset expected, dst offset expected:
    test_dst("Asia/Gaza", 2019, 9, 25, 22, 59, 59, 2, 1);
    test_dst("Asia/Gaza", 2019, 9, 25, 23, 0, 0, 2, 0);

    // Asia/Gaza  Fri Oct 23 21:59:59 2020 UT = Sat Oct 24 00:59:59 2020 EEST isdst=1 gmtoff=10800
    // Asia/Gaza  Fri Oct 23 22:00:00 2020 UT = Sat Oct 24 00:00:00 2020 EET isdst=0 gmtoff=7200
    test_dst("Asia/Gaza", 2020, 9, 23, 23, 59, 59, 2, 1);
    test_dst("Asia/Gaza", 2020, 9, 24, 0, 0, 0, 2, 0);

    // Asia/Gaza  Thu Oct 28 21:59:59 2021 UT = Fri Oct 29 00:59:59 2021 EEST isdst=1 gmtoff=10800
    // Asia/Gaza  Thu Oct 28 22:00:00 2021 UT = Fri Oct 29 00:00:00 2021 EET isdst=0 gmtoff=7200

    test_dst("Asia/Gaza", 2021, 9, 28, 23, 59, 59, 2, 1);
    test_dst("Asia/Gaza", 2021, 9, 29, 0, 0, 0, 2, 0);

    // tzdata-2026b:
    // zdump -c 2025,2027 -v America/Vancouver
    // America/Vancouver  Sun Mar  9 09:59:59 2025 UT = Sun Mar  9 01:59:59 2025 PST isdst=0 gmtoff=-28800
    // America/Vancouver  Sun Mar  9 10:00:00 2025 UT = Sun Mar  9 03:00:00 2025 PDT isdst=1 gmtoff=-25200
    // America/Vancouver  Sun Nov  2 08:59:59 2025 UT = Sun Nov  2 01:59:59 2025 PDT isdst=1 gmtoff=-25200
    // America/Vancouver  Sun Nov  2 09:00:00 2025 UT = Sun Nov  2 01:00:00 2025 PST isdst=0 gmtoff=-28800
    // America/Vancouver  Sun Mar  8 09:59:59 2026 UT = Sun Mar  8 01:59:59 2026 PST isdst=0 gmtoff=-28800
    // America/Vancouver  Sun Mar  8 10:00:00 2026 UT = Sun Mar  8 03:00:00 2026 PDT isdst=1 gmtoff=-25200
    // America/Vancouver  Sun Nov  1 08:59:59 2026 UT = Sun Nov  1 01:59:59 2026 PDT isdst=1 gmtoff=-25200
    // America/Vancouver  Sun Nov  1 09:00:00 2026 UT = Sun Nov  1 02:00:00 2026 MST isdst=0 gmtoff=-25200
    test_dst("America/Vancouver", 2025, 2, 9, 1, 59, 59, -8, 0);
    test_dst("America/Vancouver", 2025, 2, 9, 3, 0, 0, -8, 1);
    test_dst("America/Vancouver", 2025, 10, 2, 0, 59, 59, -8, 1);
    test_dst("America/Vancouver", 2025, 10, 2, 1, 59, 59, -8, 0);
    test_dst("America/Vancouver", 2026, 2, 8, 1, 59, 59, -8, 0);
    test_dst("America/Vancouver", 2026, 2, 8, 3, 0, 0, -8, 1);
    test_dst("America/Vancouver", 2026, 10, 1, 0, 59, 59, -8, 1);
    // test_dst("America/Vancouver", 2026, 10, 1, 2, 0, 0, -8, 0); // tzdata < 2026b
    test_dst("America/Vancouver", 2026, 10, 1, 2, 0, 0, -8, 1); // tzdata >= 2026b

    // Print summary and exit with the number of failed test or
    // exit with 0 if all tests passed.
    printf("Summary: %d tests failed, %d tests passed.\n",
           fail_count, pass_count);
    if (fail_count != 0) {
        printf("FAIL\n");
        exit(fail_count);
    }
    printf("PASS\n");
    exit(0);
}
