"""Tests for Total Users and Enrollment report."""

from contextlib import contextmanager
import datetime
import textwrap
from StringIO import StringIO
from unittest import TestCase

import luigi
import luigi.hdfs
from mock import MagicMock
from numpy import isnan
import pandas

from edx.analytics.tasks.reports.total_enrollments import TotalUsersAndEnrollmentsByWeek, TOTAL_ENROLLMENT_ROWNAME


class FakeTarget(object):
    """
    Fake luigi like target that saves data in memory, using a
    StringIO buffer.
    """
    def __init__(self, value=''):
        self.buffer = StringIO(value)
        # Rewind the buffer head so the value can be read
        self.buffer.seek(0)

    @contextmanager
    def open(self, *args, **kwargs):
        yield self.buffer

        # Rewind the head for easy reading
        self.buffer.seek(0)


class TestTotalUsersAndEnrollmentsByWeek(TestCase):
    """Tests for TotalUsersAndEnrollmentsByWeek class."""

    def run_task(self, source, date, weeks, offset=None, history=None):
        """
        Run task with fake targets.

        Returns:
            the task output as a pandas dataframe.
        """

        parsed_date = datetime.datetime.strptime(date, '%Y-%m-%d').date()

        # Make offsets None if it was not specified.
        task = TotalUsersAndEnrollmentsByWeek(
            source='fake_source',
            offsets='fake_offsets' if offset else None,
            history='fake_history' if history else None,
            destination='fake_destination',
            date=parsed_date,
            weeks=weeks
        )

        # Mock the input and output targets

        def reformat(string):
            # Reformat string to make it like a hadoop tsv
            return textwrap.dedent(string).strip().replace(' ', '\t')

        input_targets = {
            'source': FakeTarget(reformat(source)),
        }

        # Mock offsets only if specified.
        if offset:
            input_targets.update({'offsets': FakeTarget(reformat(offset))})

        # Mock history only if specified.
        if history:
            input_targets.update({'history': FakeTarget(reformat(history))})

        task.input = MagicMock(return_value=input_targets)

        output_target = FakeTarget()
        task.output = MagicMock(return_value=output_target)

        # Run the task and parse the output into a pandas dataframe

        task.run()

        data = output_target.buffer.read()
        result = pandas.read_csv(StringIO(data),
                                 na_values=['-'],
                                 index_col='name')

        return result

    def test_parse_source(self):
        source = """
        course_1 2013-01-01 10
        course_1 2013-01-02 10
        course_1 2013-01-03 10
        course_1 2013-01-09 10
        course_1 2013-01-17 10
        course_2 2013-01-01 10
        course_3 2013-01-01 10
        """
        res = self.run_task(source, '2013-01-17', 3)
        # self.assertEqual(set(['name']), set(res.index))
        self.assertEqual(set(['2013-01-03', '2013-01-10', '2013-01-17']),
                         set(res.columns))

        self.assertEqual(res.loc[TOTAL_ENROLLMENT_ROWNAME]['2013-01-03'], 50)
        self.assertEqual(res.loc[TOTAL_ENROLLMENT_ROWNAME]['2013-01-10'], 60)
        self.assertEqual(res.loc[TOTAL_ENROLLMENT_ROWNAME]['2013-01-17'], 70)

    def test_week_grouping(self):
        source = """
        course_1 2013-01-06 10
        course_1 2013-01-14 10
        """
        res = self.run_task(source, '2013-01-21', 4)
        weeks = set(['2012-12-31', '2013-01-07', '2013-01-14', '2013-01-21'])
        self.assertEqual(weeks, set(str(w) for w in res.columns))
        total_enrollment = res.loc[TOTAL_ENROLLMENT_ROWNAME]
        self.assertTrue(isnan(total_enrollment['2012-12-31']))  # no data
        self.assertEqual(total_enrollment['2013-01-07'], 10)
        self.assertEqual(total_enrollment['2013-01-14'], 20)
        self.assertTrue(isnan(total_enrollment['2013-01-21']))  # no data

    def test_cumulative(self):
        source = """
        course_1 2013-02-01 4
        course_1 2013-02-04 4
        course_1 2013-02-08 5
        course_1 2013-02-12 -4
        course_1 2013-02-16 6
        course_1 2013-02-18 6
        course_2 2013-02-12 2
        course_2 2013-02-14 3
        course_2 2013-02-15 -2
        """
        res = self.run_task(source, '2013-02-18', 2)
        total_enrollment = res.loc[TOTAL_ENROLLMENT_ROWNAME]
        self.assertEqual(total_enrollment['2013-02-11'], 13)
        self.assertEqual(total_enrollment['2013-02-18'], 24)

    def test_offsets(self):
        source = """
        course_1 2013-03-01 1
        course_1 2013-03-30 2
        course_2 2013-03-07 1
        course_2 2013-03-08 1
        course_2 2013-03-10 1
        course_2 2013-03-13 1
        course_3 2013-03-15 1
        course_3 2013-03-18 1
        course_3 2013-03-19 1
        """

        offset = """
        course_2 2013-03-07 8
        course_3 2013-03-15 6
        """
        res = self.run_task(source, '2013-03-28', 4, offset=offset)
        total_enrollment = res.loc[TOTAL_ENROLLMENT_ROWNAME]
        self.assertEqual(total_enrollment['2013-03-07'], 10)
        self.assertEqual(total_enrollment['2013-03-14'], 13)
        self.assertEqual(total_enrollment['2013-03-21'], 22)
        self.assertEqual(total_enrollment['2013-03-28'], 22)

    def test_unicode(self):
        course_id = u'course_\u2603'

        source = u"""
        {course_id} 2013-04-01 1
        {course_id} 2013-04-02 1
        """.format(course_id=course_id)

        res = self.run_task(source.encode('utf-8'), '2013-04-02', 1)

        self.assertEqual(res.loc[TOTAL_ENROLLMENT_ROWNAME]['2013-04-02'], 2)

    def test_task_urls(self):
        date = datetime.date(2013, 01, 20)

        task = TotalUsersAndEnrollmentsByWeek(source='s3://bucket/path/',
                                              offsets='s3://bucket/file.txt',
                                              destination='file://path/file.txt',
                                              date=date)

        requires = task.requires()

        source = requires['source'].output()
        self.assertIsInstance(source, luigi.hdfs.HdfsTarget)
        self.assertEqual(source.format, luigi.hdfs.PlainDir)

        offsets = requires['offsets'].output()
        self.assertIsInstance(offsets, luigi.hdfs.HdfsTarget)
        self.assertEqual(offsets.format, luigi.hdfs.Plain)

        destination = task.output()
        self.assertIsInstance(destination, luigi.File)
