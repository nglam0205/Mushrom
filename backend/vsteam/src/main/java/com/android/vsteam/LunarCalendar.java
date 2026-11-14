package com.android.vsteam;

import android.annotation.SuppressLint;

import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;

public class LunarCalendar {

    private static final double PI = Math.PI;

    // Dùng múi giờ Việt Nam
    private static final double TIME_ZONE = 7.0;

    public static class LunarDate {
        public int day;
        public int month;
        public int year;
        public boolean leap;

        public LunarDate(int day, int month, int year, boolean leap) {
            this.day = day;
            this.month = month;
            this.year = year;
            this.leap = leap;
        }

        @Override
        public String toString() {
            return day + "/" + month + (leap ? " (Nhuận)" : "") + "/" + year;
        }

        /**
         * Format lunar date theo pattern
         */
        @SuppressLint("DefaultLocale")
        public String format(String pattern) {
            String result = pattern;

            // Thay thế theo thứ tự từ pattern dài nhất đến ngắn nhất để tránh conflict
            // Year patterns
            result = result.replace("YYYY", String.valueOf(year));
            result = result.replace("yyyy", String.valueOf(year));
            result = result.replace("YY", String.format("%02d", year % 100));
            result = result.replace("yy", String.format("%02d", year % 100));

            // Month patterns - thay dài trước, ngắn sau
            result = result.replace("MM", String.format("%02d", month));
            result = result.replace("mm", String.format("%02d", month));

            // Day patterns - thay dài trước, ngắn sau  
            result = result.replace("DD", String.format("%02d", day));
            result = result.replace("dd", String.format("%02d", day));

            // Leap patterns
            if (leap) {
                result = result.replace("(L)", "(Nhuận)");
                result = result.replace("(l)", "(Nhuận)");
            } else {
                result = result.replace("(L)", "");
                result = result.replace("(l)", "");
            }

            // Single character patterns - chỉ thay thế nếu không nằm trong từ
            // Month - single M
            result = replaceStandalonePattern(result, "M", String.valueOf(month));

            // Day - single D  
            result = replaceStandalonePattern(result, "D", String.valueOf(day));

            // Leap - single L
            if (leap) {
                result = replaceStandalonePattern(result, "L", "Nhuận");
                result = replaceStandalonePattern(result, "l", "Nhuận");
            } else {
                result = replaceStandalonePattern(result, "L", "");
                result = replaceStandalonePattern(result, "l", "");
            }

            return result;
        }

        /**
         * Thay thế pattern chỉ khi nó đứng riêng (không nằm trong từ)
         */
        private String replaceStandalonePattern(String text, String pattern, String replacement) {
            // Sử dụng regex để chỉ match pattern khi nó không được bao quanh bởi chữ cái
            return text.replaceAll("(?<![a-zA-ZÀ-ỹ])" + pattern + "(?![a-zA-ZÀ-ỹ])", replacement);
        }
    }

    // Method gốc - giữ nguyên để backward compatibility
    public static LunarDate convertSolar2Lunar(int dd, int mm, int yy) {
        return convertSolar2Lunar(dd, mm, yy, TIME_ZONE);
    }

    public static LunarDate convertSolar2Lunar(int dd, int mm, int yy, double timeZone) {
        int dayNumber = jdFromDate(dd, mm, yy);
        int k = (int)((dayNumber - 2415021.076998695) / 29.530588853);
        int monthStart = getNewMoonDay(k + 1, timeZone);
        if (monthStart > dayNumber) {
            monthStart = getNewMoonDay(k, timeZone);
        }
        int a11 = getLunarMonth11(yy, timeZone);
        int b11 = a11;
        int lunarYear;
        if (a11 >= monthStart) {
            lunarYear = yy;
            a11 = getLunarMonth11(yy - 1, timeZone);
        } else {
            lunarYear = yy + 1;
            b11 = getLunarMonth11(yy + 1, timeZone);
        }
        int lunarDay = dayNumber - monthStart + 1;
        int diff = (int)((monthStart - a11) / 29);
        int lunarMonth = diff + 11;
        boolean leap = false;
        if (b11 - a11 > 365) {
            int leapMonthDiff = getLeapMonthOffset(a11, timeZone);
            if (diff >= leapMonthDiff) {
                lunarMonth = diff + 10;
                if (diff == leapMonthDiff) leap = true;
            }
        }
        if (lunarMonth > 12) lunarMonth -= 12;
        if (lunarMonth >= 11 && diff < 4) lunarYear -= 1;
        return new LunarDate(lunarDay, lunarMonth, lunarYear, leap);
    }

    /**
     * Chuyển đổi từ String date sang String lunar date với cùng format
     * Hỗ trợ các format: dd/MM/yyyy, dd-MM-yyyy, yyyy-MM-dd, MM/dd/yyyy
     * HOẶC tự động nhận diện pattern trong string và thay thế bằng ngày âm lịch hiện tại
     */
    public static String convertSolar2Lunar(String solarDateStr) {
        // Kiểm tra xem string có chứa date patterns không
        if (containsDatePatterns(solarDateStr)) {
            return replaceWithCurrentLunarDate(solarDateStr);
        }

        try {
            String format = detectDateFormat(solarDateStr);
            SimpleDateFormat sdf = new SimpleDateFormat(format, Locale.getDefault());
            Date date = sdf.parse(solarDateStr);

            Calendar cal = Calendar.getInstance();
            assert date != null;
            cal.setTime(date);

            LunarDate lunarDate = convertSolar2Lunar(
                    cal.get(Calendar.DAY_OF_MONTH),
                    cal.get(Calendar.MONTH) + 1,
                    cal.get(Calendar.YEAR)
            );

            return formatLunarDate(lunarDate, format);
        } catch (ParseException e) {
            throw new IllegalArgumentException("Không thể parse ngày: " + solarDateStr, e);
        }
    }

    /**
     * Kiểm tra xem string có chứa date patterns không
     */
    private static boolean containsDatePatterns(String text) {
        // Kiểm tra các pattern phổ biến cho date format
        return text.contains("DD") || text.contains("dd") ||
                text.contains("MM") || text.contains("mm") ||
                text.contains("YYYY") || text.contains("yyyy") ||
                text.contains("YY") || text.contains("yy") ||
                (text.contains("D") && !text.matches(".*[a-zA-Z]D[a-zA-Z].*")) ||
                (text.contains("M") && !text.matches(".*[a-zA-Z]M[a-zA-Z].*")) ||
                text.contains("(L)") || text.contains("(l)") ||
                (text.contains("L") && !text.matches(".*[a-zA-ZÀ-ỹ]L[a-zA-ZÀ-ỹ].*")) ||
                (text.contains("l") && !text.matches(".*[a-zA-ZÀ-ỹ]l[a-zA-ZÀ-ỹ].*"));
    }

    /**
     * Thay thế các pattern trong string bằng ngày âm lịch hiện tại
     * Ví dụ: "Ngày DD, tháng MM" → "Ngày 26, tháng 06"
     */
    private static String replaceWithCurrentLunarDate(String text) {
        // Lấy ngày âm lịch hiện tại
        Calendar today = Calendar.getInstance();
        LunarDate lunarDate = convertSolar2Lunar(
                today.get(Calendar.DAY_OF_MONTH),
                today.get(Calendar.MONTH) + 1,
                today.get(Calendar.YEAR)
        );

        // Thay thế các pattern trong text
        return lunarDate.format(text);
    }

    /**
     * Lấy ngày âm lịch hiện tại theo format pattern
     * Ví dụ: "ÂL DD/MM" → "15/11", "ÂL DD/MM/YYYY" → "15/11/2024"
     */
    public static String getCurrentLunarDate(String formatPattern) {
        // Lấy ngày hiện tại
        Calendar today = Calendar.getInstance();

        // Chuyển đổi sang âm lịch
        LunarDate lunarDate = convertSolar2Lunar(
                today.get(Calendar.DAY_OF_MONTH),
                today.get(Calendar.MONTH) + 1,
                today.get(Calendar.YEAR)
        );

        // Xử lý format pattern
        String pattern = formatPattern.toUpperCase();
        return lunarDate.format(pattern);
    }

    /**
     * Overload method để gọi trực tiếp với format pattern
     * Ví dụ: getCurrentLunarDate("DD/MM") → "15/11"
     */
    public static String getCurrentLunarDate() {
        return getCurrentLunarDate("DD/MM/YYYY");
    }

    /**
     * Lấy ngày âm lịch hiện tại với format tùy chỉnh
     */
    public static String getCurrentLunarDateWithFormat(String formatPattern) {
        return getCurrentLunarDate("ÂL " + formatPattern);
    }

    /**
     * Chuyển đổi từ Calendar sang Calendar (lunar)
     */
    public static Calendar convertSolar2Lunar(Calendar solarCalendar) {
        LunarDate lunarDate = convertSolar2Lunar(
                solarCalendar.get(Calendar.DAY_OF_MONTH),
                solarCalendar.get(Calendar.MONTH) + 1,
                solarCalendar.get(Calendar.YEAR)
        );

        Calendar lunarCalendar = Calendar.getInstance();
        lunarCalendar.set(Calendar.YEAR, lunarDate.year);
        lunarCalendar.set(Calendar.MONTH, lunarDate.month - 1); // Calendar month is 0-based
        lunarCalendar.set(Calendar.DAY_OF_MONTH, lunarDate.day);
        lunarCalendar.set(Calendar.HOUR_OF_DAY, solarCalendar.get(Calendar.HOUR_OF_DAY));
        lunarCalendar.set(Calendar.MINUTE, solarCalendar.get(Calendar.MINUTE));
        lunarCalendar.set(Calendar.SECOND, solarCalendar.get(Calendar.SECOND));

        return lunarCalendar;
    }

    /**
     * Chuyển đổi từ Date sang Date (lunar)
     */
    public static Date convertSolar2Lunar(Date solarDate) {
        Calendar cal = Calendar.getInstance();
        cal.setTime(solarDate);
        Calendar lunarCal = convertSolar2Lunar(cal);
        return lunarCal.getTime();
    }

    /**
     * Chuyển đổi với custom format pattern
     */
    public static String convertSolar2LunarWithFormat(String solarDateStr, String inputFormat, String outputFormat) {
        try {
            SimpleDateFormat sdf = new SimpleDateFormat(inputFormat, Locale.getDefault());
            Date date = sdf.parse(solarDateStr);

            Calendar cal = Calendar.getInstance();
            assert date != null;
            cal.setTime(date);

            LunarDate lunarDate = convertSolar2Lunar(
                    cal.get(Calendar.DAY_OF_MONTH),
                    cal.get(Calendar.MONTH) + 1,
                    cal.get(Calendar.YEAR)
            );

            return lunarDate.format(outputFormat);
        } catch (ParseException e) {
            throw new IllegalArgumentException("Không thể parse ngày với format " + inputFormat + ": " + solarDateStr, e);
        }
    }

    /**
     * Tự động detect format của date string
     */
    private static String detectDateFormat(String dateStr) {
        if (dateStr.matches("\\d{1,2}/\\d{1,2}/\\d{4}")) {
            return "dd/MM/yyyy";
        } else if (dateStr.matches("\\d{1,2}-\\d{1,2}-\\d{4}")) {
            return "dd-MM-yyyy";
        } else if (dateStr.matches("\\d{4}-\\d{1,2}-\\d{1,2}")) {
            return "yyyy-MM-dd";
        } else if (dateStr.matches("\\d{1,2}/\\d{1,2}/\\d{2}")) {
            return "dd/MM/yy";
        } else if (dateStr.matches("\\d{1,2}-\\d{1,2}-\\d{2}")) {
            return "dd-MM-yy";
        } else {
            // Default fallback
            return "dd/MM/yyyy";
        }
    }

    /**
     * Format lunar date theo pattern của solar date
     */
    @SuppressLint("DefaultLocale")
    private static String formatLunarDate(LunarDate lunarDate, String originalFormat) {
        String result = originalFormat;

        if (originalFormat.contains("yyyy")) {
            result = result.replace("yyyy", String.valueOf(lunarDate.year));
        } else if (originalFormat.contains("yy")) {
            result = result.replace("yy", String.format("%02d", lunarDate.year % 100));
        }

        if (originalFormat.contains("MM")) {
            result = result.replace("MM", String.format("%02d", lunarDate.month));
        } else if (originalFormat.contains("M")) {
            result = result.replace("M", String.valueOf(lunarDate.month));
        }

        if (originalFormat.contains("dd")) {
            result = result.replace("dd", String.format("%02d", lunarDate.day));
        } else if (originalFormat.contains("d")) {
            result = result.replace("d", String.valueOf(lunarDate.day));
        }

        // Thêm thông tin tháng nhuận nếu có
        if (lunarDate.leap) {
            result += " (Nhuận)";
        }

        return result;
    }

    private static int jdFromDate(int dd, int mm, int yy) {
        int a = (14 - mm) / 12;
        int y = yy + 4800 - a;
        int m = mm + 12 * a - 3;
        return dd + (153 * m + 2) / 5 + 365 * y + y / 4 - y / 100 + y / 400 - 32045;
    }

    private static int getNewMoonDay(int k, double timeZone) {
        return (int)Math.floor(jdFromNewMoon(k) + 0.5 + timeZone / 24);
    }

    private static double jdFromNewMoon(int k) {
        double T = k / 1236.85;
        double T2 = T * T;
        double T3 = T2 * T;
        double dr = PI / 180;
        double Jd1 = 2415020.75933 + 29.53058868 * k
                + 0.0001178 * T2 - 0.000000155 * T3
                + 0.00033 * Math.sin((166.56 + 132.87 * T - 0.009173 * T2) * dr);
        double M = 359.2242 + 29.10535608 * k
                - 0.0000333 * T2 - 0.00000347 * T3;
        double Mpr = 306.0253 + 385.81691806 * k
                + 0.0107306 * T2 + 0.00001236 * T3;
        double F = 21.2964 + 390.67050646 * k
                - 0.0016528 * T2 - 0.00000239 * T3;
        double C1 = (0.1734 - 0.000393 * T) * Math.sin(M * dr)
                + 0.0021 * Math.sin(2 * dr * M)
                - 0.4068 * Math.sin(Mpr * dr)
                + 0.0161 * Math.sin(dr * 2 * Mpr)
                - 0.0004 * Math.sin(dr * 3 * Mpr)
                + 0.0104 * Math.sin(dr * 2 * F)
                - 0.0051 * Math.sin(dr * (M + Mpr))
                - 0.0074 * Math.sin(dr * (M - Mpr))
                + 0.0004 * Math.sin(dr * (2 * F + M))
                - 0.0004 * Math.sin(dr * (2 * F - M))
                - 0.0006 * Math.sin(dr * (2 * F + Mpr))
                + 0.0010 * Math.sin(dr * (2 * F - Mpr))
                + 0.0005 * Math.sin(dr * (2 * Mpr + M));
        double deltaT = T < -11 ? 0.001 + 0.000839 * T + 0.0002261 * T2
                - 0.00000845 * T3 - 0.000000081 * T * T3
                : -0.000278 + 0.000265 * T + 0.000262 * T2;
        return Jd1 + C1 - deltaT;
    }

    private static int getLunarMonth11(int yy, double timeZone) {
        int off = jdFromDate(31, 12, yy) - 2415021;
        int k = (int)(off / 29.530588853);
        int nm = getNewMoonDay(k, timeZone);
        int sunLong = getSunLongitude(nm, timeZone);
        if (sunLong >= 9) {
            nm = getNewMoonDay(k - 1, timeZone);
        }
        return nm;
    }

    private static int getSunLongitude(int jdn, double timeZone) {
        return (int)(sunLongitude(jdn - 0.5 - timeZone / 24) / PI * 6);
    }

    private static double sunLongitude(double jdn) {
        double T = (jdn - 2451545.0) / 36525;
        double T2 = T * T;
        double dr = PI / 180;
        double M = 357.52910 + 35999.05030 * T
                - 0.0001559 * T2 - 0.00000048 * T * T2;
        double L0 = 280.46645 + 36000.76983 * T
                + 0.0003032 * T2;
        double DL = (1.914600 - 0.004817 * T - 0.000014 * T2) * Math.sin(dr * M)
                + (0.019993 - 0.000101 * T) * Math.sin(dr * 2 * M)
                + 0.000290 * Math.sin(dr * 3 * M);
        double L = L0 + DL;
        L = L * dr;
        return L - PI * 2 * (int)(L / (PI * 2));
    }

    private static int getLeapMonthOffset(int a11, double timeZone) {
        int k = (int)((a11 - 2415021.076998695) / 29.530588853 + 0.5);
        int last = getSunLongitude(getNewMoonDay(k, timeZone), timeZone);
        int i = 1;
        int arc = getSunLongitude(getNewMoonDay(k + i, timeZone), timeZone);
        while (arc != last && i < 14) {
            last = arc;
            i++;
            arc = getSunLongitude(getNewMoonDay(k + i, timeZone), timeZone);
        }
        return i - 1;
    }
}
