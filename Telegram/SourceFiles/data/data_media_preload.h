/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#pragma once

#include "base/weak_ptr.h"
#include "storage/download_manager_mtproto.h"

namespace Data {

class PhotoMedia;
struct FileOrigin;

class MediaPreload {
public:
	explicit MediaPreload(Fn<void(bool)> done);
	virtual ~MediaPreload() = default;

protected:
	void callDone(bool success);

private:
	Fn<void(bool)> _done;

};

class PhotoPreload final : public MediaPreload, public base::has_weak_ptr {
public:
	enum class Completion {
		Progressive,
		Full,
	};

	[[nodiscard]] static bool Should(
		not_null<PhotoData*> photo,
		not_null<PeerData*> context,
		bool retryCancelled = false);

	PhotoPreload(
		not_null<PhotoData*> data,
		FileOrigin origin,
		Fn<void(bool)> done,
		bool retryCancelled = true,
		Completion completion = Completion::Progressive);
	PhotoPreload(
		not_null<PhotoData*> data,
		FileOrigin origin,
		Fn<void()> done,
		bool retryCancelled = true,
		Completion completion = Completion::Progressive);
	~PhotoPreload();

private:
	void start(
		FileOrigin origin,
		bool retryCancelled,
		Completion completion);
	void sessionCleared();

	std::shared_ptr<PhotoMedia> _photo;
	rpl::lifetime _lifetime;
	rpl::lifetime _sessionLifetime;
	bool _registered = false;
	bool _requireFull = false;

};

class VideoPreload final
	: public MediaPreload
	, private Storage::DownloadMtprotoTask {
public:
	[[nodiscard]] static bool Can(not_null<DocumentData*> video);

	VideoPreload(
		not_null<DocumentData*> video,
		FileOrigin origin,
		Fn<void(bool)> done);
	VideoPreload(
		not_null<DocumentData*> video,
		FileOrigin origin,
		Fn<void()> done);
	~VideoPreload();

private:
	void check();
	void load();
	void done(QByteArray result);

	bool readyToRequest() const override;
	int64 takeNextRequestOffset() override;
	bool feedPart(int64 offset, const QByteArray &bytes) override;
	void cancelOnFail() override;
	bool setWebFileSizeHook(int64 size) override;

	const not_null<DocumentData*> _video;
	base::flat_map<uint32, QByteArray> _parts;
	base::flat_set<int> _requestedOffsets;
	int64 _full = 0;
	int _nextRequestOffset = 0;
	bool _finished = false;
	bool _failed = false;

};

} // namespace Data
