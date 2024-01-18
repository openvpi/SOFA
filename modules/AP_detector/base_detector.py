class BaseAPDetector:
    def __init__(self, **kwargs):
        # args: list of str
        pass

    def process(self, predictions):
        # input: list of predictions, each prediction is a tuple of:
        #     wav_path: pathlib.Path
        #     ph_seq: list of phonemes, SP is the silence phoneme.
        #     ph_intervals: np.ndarray of shape (n_ph, 2), ph_intervals[i] = [start, end]
        #                   means the i-th phoneme starts at start and ends at end.
        #     word_seq: list of words.
        #     word_intervals: np.ndarray of shape (n_word, 2), word_intervals[i] = [start, end]

        # output: same as the input.

        res = []
        for (
            wav_path,
            wav_length,
            ph_seq,
            ph_intervals,
            word_seq,
            word_intervals,
        ) in predictions:
            res.append(
                self._process_one(
                    wav_path, wav_length, ph_seq, ph_intervals, word_seq, word_intervals
                )
            )

        return res

    def _process_one(
        self, wav_path, wav_length, ph_seq, ph_intervals, word_seq, word_intervals
    ):
        # input:
        #     wav_path: pathlib.Path
        #     ph_seq: list of phonemes, SP is the silence phoneme.
        #     ph_intervals: np.ndarray of shape (n_ph, 2), ph_intervals[i] = [start, end]
        #                   means the i-th phoneme starts at start and ends at end.
        #     word_seq: list of words.
        #     word_intervals: np.ndarray of shape (n_word, 2), word_intervals[i] = [start, end]

        # output: same as the input.
        raise NotImplementedError
