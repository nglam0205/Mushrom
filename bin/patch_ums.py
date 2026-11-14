import json
import sys

if len(sys.argv) < 2:
    print("Usage: python3 script.py input.json")
    sys.exit(1)

file_path = sys.argv[1]

# ===== DATA APPEND =====
append_data = [
    {
    "groupId": 22200082,
    "groupTitle": "@oplus_card_name",
    "groupIcon": "@ic_app",
    "type": 222221000,
    "name": "拨号",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 47,
    "packageName": "com.android.providers.contacts",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.customize.card.ContactsCardWidgetProvider",
    "minColorOSVer": 37,
    "serviceId": "536872000",
    "appLimit": {
      "operation": "and",
      "limitGroup": [
        {
          "operation": "and",
          "limitList": [
            {
              "key": "localApp",
              "ext": "com.android.contacts",
              "op": 0,
              "list": []
            }
          ]
        }
      ]
    }
  },
  {
    "groupId": 22200082,
    "groupTitle": "@oplus_card_name",
    "groupIcon": "@ic_app",
    "type": 222221001,
    "name": "拨号",
    "size": 5,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 47,
    "packageName": "com.android.providers.contacts",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.customize.card.ContactsCardWidgetProvider",
    "minColorOSVer": 37,
    "serviceId": "536872000",
    "appLimit": {
      "operation": "and",
      "limitGroup": [
        {
          "operation": "and",
          "limitList": [
            {
              "key": "localApp",
              "ext": "com.android.contacts",
              "op": 0,
              "list": []
            }
          ]
        }
      ]
    }
  },
  {
    "groupId": 25,
    "groupTitle": "便签",
    "groupIcon": "@ic_app",
    "type": 222221010,
    "name": "新建笔记",
    "size": 10,
    "supportActiveUpdate": True,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 7,
    "packageName": "com.coloros.note",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.oplus.note.card.note.NoteLockScreenCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536872010"
  },
  {
    "groupId": 25,
    "groupTitle": "便签",
    "groupIcon": "@ic_app",
    "type": 222221020,
    "name": "新建笔记",
    "size": 10,
    "supportActiveUpdate": True,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 7,
    "packageName": "com.oneplus.note",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.oplus.note.card.note.NoteLockScreenCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536872020"
  },
  {
    "groupId": 222000111,
    "groupTitle": "计算器",
    "groupIcon": "@ic_app",
    "orderInGroup": 1,
    "type": 200057,
    "name": "计算器",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 1,
    "packageName": "com.coloros.calculator",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.android.calculator2.card.CalculatorExportLockScreenCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878479"
  },
  {
    "groupId": 222000111,
    "groupTitle": "计算器",
    "groupIcon": "@ic_app",
    "orderInGroup": 1,
    "type": 200057,
    "name": "计算器",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 1,
    "packageName": "com.oneplus.calculator",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.android.calculator2.card.CalculatorExportLockScreenCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878479"
  },
  {
    "groupId": 21,
    "groupTitle": "时钟",
    "groupIcon": "@ic_app",
    "orderInGroup": 1,
    "type": 200085,
    "name": "闹钟",
    "size": 5,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 12,
    "packageName": "com.coloros.alarmclock",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.oplus.alarmclock.provider.ClockExportCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878486"
  },
  {
    "groupId": 21,
    "groupTitle": "时钟",
    "groupIcon": "@ic_app",
    "orderInGroup": 1,
    "type": 200085,
    "name": "闹钟",
    "size": 5,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 12,
    "packageName": "com.oneplus.deskclock",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.oplus.alarmclock.provider.ClockExportCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878486"
  },
  {
    "groupId": 21,
    "groupTitle": "时钟",
    "groupIcon": "@ic_app",
    "orderInGroup": 4,
    "type": 200086,
    "name": "秒表",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 12,
    "packageName": "com.coloros.alarmclock",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.oplus.alarmclock.provider.ClockExportCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878489"
  },
  {
    "groupId": 21,
    "groupTitle": "时钟",
    "groupIcon": "@ic_app",
    "orderInGroup": 4,
    "type": 200086,
    "name": "秒表",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 12,
    "packageName": "com.oneplus.deskclock",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.oplus.alarmclock.provider.ClockExportCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878489"
  },
  {
    "groupId": 21,
    "groupTitle": "时钟",
    "groupIcon": "@ic_app",
    "orderInGroup": 3,
    "type": 200088,
    "name": "计时",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 12,
    "packageName": "com.coloros.alarmclock",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.oplus.alarmclock.provider.ClockExportCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878488"
  },
  {
    "groupId": 21,
    "groupTitle": "时钟",
    "groupIcon": "@ic_app",
    "orderInGroup": 3,
    "type": 200088,
    "name": "计时",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 12,
    "packageName": "com.oneplus.deskclock",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.oplus.alarmclock.provider.ClockExportCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878488"
  },
  {
    "groupId": 21,
    "groupTitle": "时钟",
    "groupIcon": "@ic_app",
    "orderInGroup": 2,
    "type": 200084,
    "name": "闹钟",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 12,
    "packageName": "com.coloros.alarmclock",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.oplus.alarmclock.provider.ClockExportCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878486"
  },
  {
    "groupId": 21,
    "groupTitle": "时钟",
    "groupIcon": "@ic_app",
    "orderInGroup": 2,
    "type": 200084,
    "name": "闹钟",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 12,
    "packageName": "com.oneplus.deskclock",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.oplus.alarmclock.provider.ClockExportCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878486"
  },
  {
    "groupId": 23,
    "groupTitle": "时钟",
    "groupIcon": "@ic_app",
    "orderInGroup": 2,
    "type": 200084,
    "name": "闹钟",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 12,
    "packageName": "com.oplus.calendar",
    "displayArea": 64,
    "reservedFlag": 15,
    "componentName": "com.android.calendar.seedling.CarEventCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878486"
  },
  {
    "groupId": 15,
    "groupTitle": "设备空间",
    "groupIcon": "@ic_app",
    "orderInGroup": 2,
    "type": 100774,
    "name": "自选设备",
    "desc": "查看和控制单个设备",
    "size": 10,
    "supportActiveUpdate": True,
    "minActiveUpdateInterval": 1,
    "category": 1,
    "groupOrder": 11,
    "packageName": "com.heytap.mydevices",
    "displayArea": 64,
    "reservedFlag": 0,
    "componentName": "com.oplus.mydevices.remotecards.provider.DeviceLiteCardWidgetProvider",
    "minColorOSVer": 37,
    "serviceId": "536878705"
  },
  {
    "groupId": 15,
    "groupTitle": "设备空间",
    "groupIcon": "@ic_app",
    "orderInGroup": 1,
    "type": 100775,
    "name": "自选设备",
    "desc": "查看和控制单个设备",
    "size": 5,
    "supportActiveUpdate": True,
    "minActiveUpdateInterval": 1,
    "category": 1,
    "groupOrder": 11,
    "packageName": "com.heytap.mydevices",
    "displayArea": 64,
    "reservedFlag": 0,
    "componentName": "com.oplus.mydevices.remotecards.provider.DeviceLiteCardWidgetProvider",
    "minColorOSVer": 37,
    "serviceId": "536878705"
  },
  {
    "groupId": 22,
    "groupTitle": "天气",
    "groupIcon": "@ic_app",
    "orderInGroup": 5,
    "type": 100800,
    "name": "实时天气",
    "desc": "查看城市实时天气",
    "size": 10,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 1,
    "groupOrder": 3,
    "packageName": "com.coloros.weather2",
    "displayArea": 64,
    "reservedFlag": 0,
    "componentName": "com.oplus.weather.quickcard.WeatherRpkCardProvider",
    "minColorOSVer": 37,
    "serviceId": "536878709"
  },
  {
    "groupId": 1000026,
    "groupTitle": "日历",
    "groupIcon": "@ic_launcher",
    "orderInGroup": 5,
    "type": 200131,
    "name": "月历",
    "size": 1,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 100,
    "groupOrder": 60,
    "packageName": "com.oplus.calendar",
    "componentName": "com.android.calendar.seedling.CarEventCardProvider",
    "defaultSubscribed": 0,
    "minColorOSVer": 34,
    "displayArea": 64,
    "serviceId": "536875395"
  },
  {
    "groupId": 1000026,
    "groupTitle": "日历",
    "groupIcon": "@ic_launcher",
    "orderInGroup": 8,
    "type": 201029,
    "name": "生日",
    "size": 1,
    "supportActiveUpdate": False,
    "minActiveUpdateInterval": 0,
    "category": 100,
    "groupOrder": 60,
    "displayArea": 64,
    "packageName": "com.oplus.calendar",
    "componentName": "com.android.calendar.seedling.CarEventCardProvider",
    "defaultSubscribed": 0,
    "minColorOSVer": 34,
    "serviceId": "536877063"
  }
]

# ===== LOAD =====
with open(file_path, "r", encoding="utf-8") as f:
    data = json.load(f)

# ===== REMOVE displayArea = 64 =====
if isinstance(data, list):
    data = [item for item in data if item.get("displayArea") != 64]
else:
    print("JSON root is not a list!")
    sys.exit(1)

# ===== APPEND =====
data.extend(append_data)

# ===== SAVE (overwrite) =====
with open(file_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
