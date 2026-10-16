/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#include "data/data_document.h"
#include "media/audio/media_audio.h"
#include "media/streaming/media_streaming_utility.h"

#include <QtCore/QTimer>

namespace Media::Audio {

bool SupportsSpeedControl() {
	return true;
}

} // namespace Media::Audio

namespace Media::Player {

Mixer *mixer() {
	return nullptr;
}

rpl::producer<AudioMsgId> Updated() {
	return rpl::never<AudioMsgId>();
}

void Mixer::feedFromExternal(ExternalSoundPart &&part) {
}

void Mixer::play(
		const AudioMsgId &audio,
		std::unique_ptr<ExternalSoundData> externalData,
		crl::time positionMs) {
}

void Mixer::pause(const AudioMsgId &audio, bool fast) {
}

void Mixer::resume(const AudioMsgId &audio, bool fast) {
}

void Mixer::stop(const AudioMsgId &audio) {
}

void Mixer::forceToBufferExternal(const AudioMsgId &audioId) {
}

void Mixer::setSpeedFromExternal(
		const AudioMsgId &audioId,
		float64 speed) {
}

Streaming::TimePoint Mixer::getExternalSyncTimePoint(
		const AudioMsgId &audioId) const {
	return {};
}

TrackState Mixer::currentState(AudioMsgId::Type type) {
	return {};
}

} // namespace Media::Player

namespace crl {

namespace {

class MainUpdateRequests final {
public:
	MainUpdateRequests() {
		_timer.setInterval(16);
		QObject::connect(
			&_timer,
			&QTimer::timeout,
			[=] { _requests.fire({}); });
		_timer.start();
	}

	rpl::producer<> events() const {
		return _requests.events();
	}

private:
	rpl::event_stream<> _requests;
	QTimer _timer;

};

} // namespace

rpl::producer<> on_main_update_requests() {
	static auto requests = MainUpdateRequests();
	return requests.events();
}

} // namespace crl

crl::time DocumentData::duration() const {
	return 0;
}

bool DocumentData::isVoiceMessage() const {
	return false;
}

bool DocumentData::isVideoMessage() const {
	return false;
}

bool DocumentData::isAudioFile() const {
	return false;
}

bool DocumentData::isVideoFile() const {
	return false;
}
