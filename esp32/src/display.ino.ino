// ESP32 + EL073TS3 + GxEPD2 + 仪表盘整图显示

#include <SPI.h>
#include <GxEPD2_BW.h>
#include <GxEPD2_3C.h>
#include <GxEPD2_4C.h>
#include <GxEPD2_7C.h>
#include <Fonts/FreeMonoBold9pt7b.h>

#include "dashboard7c_800x480.h"   // <<—— 刚刚生成的头文件

// 卖家提供的构造方式（7.3 七色 / 四色屏通用）
GxEPD2_7C<
  GxEPD2_730c_GDEY073D46,
  GxEPD2_730c_GDEY073D46::HEIGHT / 4
> display(
  GxEPD2_730c_GDEY073D46(
    /*CS=*/33,
    /*DC=*/27,
    /*RST=*/26,
    /*BUSY=*/25
  )
);

void drawDashboard();

void setup()
{
  Serial.begin(115200);
  delay(100);
  Serial.println();
  Serial.println("===== ESP32 + EL073TS3 Dashboard =====");

  SPI.end();
  SPI.begin(13, 12, 14, 15);

  display.init(115200, true, 2, false);
  display.setRotation(1);

  drawDashboard();

  // 为了观察效果，你可以先注释掉这句，看屏幕停留在最终画面多久
  // display.hibernate();
}

void loop()
{
}

// 画整张仪表盘（GoodDisplay demo 格式）
void drawDashboard()
{
  Serial.println("[DRAW] dashboard7c_800x480");

  display.setFullWindow();

  // 这里千万不要再套 firstPage/nextPage 了
  display.epd2.drawDemoBitmap(
    dashboard7c_800x480,  // 你的帧缓冲
    0,                     // x
    0,                     // y
    0,                     // 预留，示例里就是 0
    800,                   // width
    480,                   // height
    0,                     // page_height = 0
    false,                 // invert = false
    true                   // pgm = true (在 PROGMEM)
  );

  Serial.println("[DRAW] done.");
}