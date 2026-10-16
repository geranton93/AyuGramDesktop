/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#pragma once

#include "data/data_file_origin.h"

#include <QtCore/QString>

#include <memory>
#include <vector>

class History;
class PhotoData;
class DocumentData;
struct FilePrepareResult;

namespace MTP {
class Error;
} // namespace MTP

namespace Data {
struct InputVenue;
} // namespace Data

namespace Main {
class Session;
} // namespace Main

namespace Api {

enum class SendResult {
	Succeeded,
	Failed,
	Indeterminate,
};

[[nodiscard]] SendResult SendResultForError(const MTP::Error &error);

class SendRequest final {
public:
	SendRequest(
		int expectedResults,
		FnMut<void(SendResult)> callback,
		Fn<bool()> shouldCancel = nullptr);
	~SendRequest();

	void finishOne(SendResult result);
	void registerCancellation(Fn<void()> cancellation);
	void cancel();
	[[nodiscard]] bool shouldCancel() const;

private:
	struct State;
	std::shared_ptr<State> _state;
};

struct MessageToSend;
struct SendAction;
struct MusicSelectionItem {
	not_null<DocumentData*> document;
	Data::FileOrigin origin;
};

void SendExistingDocument(
	MessageToSend &&message,
	not_null<DocumentData*> document,
	std::optional<MsgId> localMessageId = std::nullopt,
	std::shared_ptr<SendRequest> sendRequest = nullptr);

void SendMusicSelection(
	MessageToSend &&message,
	std::vector<MusicSelectionItem> items);

void SendExistingPhoto(
	MessageToSend &&message,
	not_null<PhotoData*> photo,
	std::optional<MsgId> localMessageId = std::nullopt);

bool SendDice(MessageToSend &message);

// We can't create Data::LocationPoint() and use it
// for a local sending message, because we can't request
// map thumbnail in messages history without access hash.
void SendLocation(SendAction action, float64 lat, float64 lon);

void SendVenue(SendAction action, Data::InputVenue venue);

void FillMessagePostFlags(
	const SendAction &action,
	not_null<PeerData*> peer,
	MessageFlags &flags);

void SendConfirmedFile(
	not_null<Main::Session*> session,
	const std::shared_ptr<FilePrepareResult> &file);

} // namespace Api
