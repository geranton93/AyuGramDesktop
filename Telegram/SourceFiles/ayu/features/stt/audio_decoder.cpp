// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026

#include "ayu/features/stt/audio_decoder.h"

#include "base/debug_log.h"
#include "gsl/util"

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libswresample/swresample.h>
}

#include <QtCore/QFileInfo>

#include <algorithm>
#include <climits>
#include <cstdint>
#include <new>

namespace Ayu::STT {
namespace {

constexpr int kTargetSampleRate = 16000;
constexpr int kMaxAudioSeconds = 15 * 60;
constexpr auto kMaxPcmSamples = std::size_t(kTargetSampleRate)
	* kMaxAudioSeconds;
constexpr qint64 kMaxInputFileBytes = 256 * 1024 * 1024;

} // namespace

std::vector<float> decodeAudioToPcm(const QString &filePath) {
	const auto fileInfo = QFileInfo(filePath);
	if (!fileInfo.isFile()
		|| fileInfo.size() <= 0
		|| fileInfo.size() > kMaxInputFileBytes) {
		LOG(("STT audio decoder: input file is missing or too large: %1").arg(
			filePath));
		return {};
	}

	const auto pathUtf8 = filePath.toUtf8();
	AVFormatContext *formatCtx = nullptr;
	if (avformat_open_input(
			&formatCtx,
			pathUtf8.constData(),
			nullptr,
			nullptr) < 0) {
		LOG(("STT audio decoder: failed to open file: %1").arg(filePath));
		return {};
	}
	const auto closeFormat = gsl::finally([&] {
		avformat_close_input(&formatCtx);
	});

	if (avformat_find_stream_info(formatCtx, nullptr) < 0
		|| formatCtx->nb_streams == 0) {
		return {};
	}
	if (formatCtx->duration != AV_NOPTS_VALUE
		&& formatCtx->duration > int64_t(kMaxAudioSeconds) * AV_TIME_BASE) {
		LOG(("STT audio decoder: audio duration exceeds the limit: %1").arg(
			filePath));
		return {};
	}

	int audioStreamIndex = -1;
	for (unsigned i = 0; i < formatCtx->nb_streams; ++i) {
		const auto stream = formatCtx->streams[i];
		if (stream && stream->codecpar
			&& stream->codecpar->codec_type == AVMEDIA_TYPE_AUDIO) {
			audioStreamIndex = (i <= static_cast<unsigned>(INT_MAX))
				? static_cast<int>(i)
				: -1;
			break;
		}
	}
	if (audioStreamIndex < 0) {
		LOG(("STT audio decoder: no audio stream in: %1").arg(filePath));
		return {};
	}

	const auto *codecParams = formatCtx->streams[audioStreamIndex]->codecpar;
	if (!codecParams
		|| codecParams->sample_rate <= 0
		|| codecParams->sample_rate > 384000
		|| codecParams->ch_layout.nb_channels <= 0
		|| codecParams->ch_layout.nb_channels > 32) {
		return {};
	}
	const auto codec = avcodec_find_decoder(codecParams->codec_id);
	if (!codec) {
		return {};
	}

	AVCodecContext *codecCtx = avcodec_alloc_context3(codec);
	if (!codecCtx) {
		return {};
	}
	const auto closeCodec = gsl::finally([&] {
		avcodec_free_context(&codecCtx);
	});
	if (avcodec_parameters_to_context(codecCtx, codecParams) < 0
		|| avcodec_open2(codecCtx, codec, nullptr) < 0
		|| codecCtx->sample_rate <= 0
		|| codecCtx->ch_layout.nb_channels <= 0) {
		return {};
	}

	constexpr AVChannelLayout targetLayout = AV_CHANNEL_LAYOUT_MONO;
	SwrContext *swrCtx = nullptr;
	if (swr_alloc_set_opts2(
			&swrCtx,
			&targetLayout,
			AV_SAMPLE_FMT_FLT,
			kTargetSampleRate,
			&codecCtx->ch_layout,
			codecCtx->sample_fmt,
			codecCtx->sample_rate,
			0,
			nullptr) < 0
		|| !swrCtx
		|| swr_init(swrCtx) < 0) {
		swr_free(&swrCtx);
		return {};
	}
	const auto closeSwr = gsl::finally([&] { swr_free(&swrCtx); });

	AVPacket *packet = av_packet_alloc();
	AVFrame *frame = av_frame_alloc();
	if (!packet || !frame) {
		av_packet_free(&packet);
		av_frame_free(&frame);
		return {};
	}
	const auto freePacket = gsl::finally([&] { av_packet_free(&packet); });
	const auto freeFrame = gsl::finally([&] { av_frame_free(&frame); });

	try {
		auto pcmData = std::vector<float>();
		pcmData.reserve(std::min<std::size_t>(
			kMaxPcmSamples,
			std::size_t(kTargetSampleRate) * 30));

		auto convertFrame = [&]() {
			if (!frame || frame->nb_samples <= 0
				|| frame->nb_samples > 10 * kTargetSampleRate) {
				return false;
			}
			const auto delay = swr_get_delay(swrCtx, codecCtx->sample_rate);
			if (delay < 0) {
				return false;
			}
			const auto outputSamples64 = av_rescale_rnd(
				delay + frame->nb_samples,
				kTargetSampleRate,
				codecCtx->sample_rate,
				AV_ROUND_UP);
			if (outputSamples64 <= 0
				|| outputSamples64 > INT_MAX
				|| static_cast<std::size_t>(outputSamples64)
					> kMaxPcmSamples - pcmData.size()) {
				return false;
			}

			const auto outputSamples = static_cast<int>(outputSamples64);
			const auto previousSize = pcmData.size();
			pcmData.resize(previousSize + outputSamples);
			auto *out = reinterpret_cast<uint8_t*>(
				pcmData.data() + previousSize);
			const auto converted = swr_convert(
				swrCtx,
				&out,
				outputSamples,
				const_cast<const uint8_t **>(
					frame->extended_data ? frame->extended_data : frame->data),
				frame->nb_samples);
			if (converted < 0 || converted > outputSamples) {
				pcmData.resize(previousSize);
				return false;
			}
			pcmData.resize(previousSize + converted);
			return pcmData.size() < kMaxPcmSamples;
		};

		auto receiveFrames = [&]() {
			for (;;) {
				const auto result = avcodec_receive_frame(codecCtx, frame);
				if (result == AVERROR(EAGAIN) || result == AVERROR_EOF) {
					return true;
				}
				if (result < 0 || !convertFrame()) {
					return false;
				}
				av_frame_unref(frame);
			}
		};

		auto readResult = 0;
		while ((readResult = av_read_frame(formatCtx, packet)) >= 0) {
			if (packet->stream_index == audioStreamIndex) {
				auto sent = avcodec_send_packet(codecCtx, packet);
				if (sent == AVERROR(EAGAIN) && !receiveFrames()) {
					av_packet_unref(packet);
					return {};
				}
				if (sent == AVERROR(EAGAIN)) {
					sent = avcodec_send_packet(codecCtx, packet);
				}
				av_packet_unref(packet);
				if (sent < 0) {
					return {};
				}
				if (!receiveFrames()) {
					return {};
				}
			} else {
				av_packet_unref(packet);
			}
		}
		if (readResult != AVERROR_EOF) {
			return {};
		}

		const auto flush = avcodec_send_packet(codecCtx, nullptr);
		if ((flush < 0 && flush != AVERROR(EAGAIN))
			|| !receiveFrames()) {
			return {};
		}

		const auto delayed = swr_get_out_samples(swrCtx, 0);
		if (delayed < 0) {
			return {};
		}
		if (delayed > 0) {
			if (delayed > INT_MAX
				|| static_cast<std::size_t>(delayed)
					> kMaxPcmSamples - pcmData.size()) {
				return {};
			}
			const auto previousSize = pcmData.size();
			const auto outputSamples = static_cast<int>(delayed);
			pcmData.resize(previousSize + outputSamples);
			auto *out = reinterpret_cast<uint8_t*>(
				pcmData.data() + previousSize);
			const auto converted = swr_convert(
				swrCtx,
				&out,
				outputSamples,
				nullptr,
				0);
			if (converted < 0 || converted > outputSamples) {
				return {};
			}
			pcmData.resize(previousSize + converted);
		}
		return pcmData;
	} catch (const std::bad_alloc &) {
		LOG(("STT audio decoder: audio buffer allocation failed: %1").arg(
			filePath));
		return {};
	}
}

} // namespace Ayu::STT
