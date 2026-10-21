// This is the source code of AyuGram for Desktop.
//
// We do and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/streamer_mode/streamer_mode.h"

#include "core/application.h"
#include "platform/platform_specific.h"
#include "window/window_controller.h"

#include <QtCore/QVariant>
#include <QtWidgets/QApplication>
#include <QtWidgets/QWidget>

namespace AyuFeatures::StreamerMode {

namespace {

constexpr auto kHiddenProperty = "AyuStreamerModeHidden";

[[nodiscard]] bool IsWindowCaptureExcluded(not_null<QWidget*> widget) {
	return widget->property(kHiddenProperty).toBool();
}

void SetWindowCaptureExcluded(QWidget *widget, bool excluded) {
	const auto window = widget->window();
	window->setProperty(kHiddenProperty, excluded);
	if (excluded) {
		Platform::SetWindowScreenshotProtection(window, true);
	}
}

} // namespace

void apply(bool enabled) {
	Core::App().enumerateWindows([=](not_null<Window::Controller*> window) {
		SetWindowCaptureExcluded(window->widget(), enabled);
	});
	for (const auto widget : QApplication::topLevelWidgets()) {
		if (!widget->windowHandle()) {
			continue;
		}
		if (enabled) {
			if (widget->isVisible()
				&& !IsWindowCaptureExcluded(widget)) {
				SetWindowCaptureExcluded(widget, true);
			}
		} else {
			widget->setProperty(kHiddenProperty, false);
		}
	}
}

void hideWidgetWindow(QWidget *widget) {
	SetWindowCaptureExcluded(widget, true);
}

void showWidgetWindow(QWidget *widget) {
	widget->window()->setProperty(kHiddenProperty, false);
}

} // namespace AyuFeatures::StreamerMode
